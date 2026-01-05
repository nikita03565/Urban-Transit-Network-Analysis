import datetime
import json
import math
import os
import re
import time
from abc import abstractmethod

import requests
from bs4 import BeautifulSoup
import hashlib
import os

"""
    Класс занимающийся парсингом данных с сайта https://kudikina.ru
"""

cache_dir = "cache"
city_urls_file = os.path.join(cache_dir, "city_urls.json")
cache_expire_days = 30
site_url = "https://kudikina.ru"
map_url = "/map"
timetable_forward_url = "/A"
timetable_backward_url = "/B"
# TODO: need to update according to city coordinates
city_avg_x_coordinate = 60.0
city_avg_y_coordinate = 30.0
request_pause_sec = 2.5


class AbstractTransportGraphParser:

    __headers = {
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-language": "en-US,en-GB;q=0.9,en;q=0.8",
        "dnt": "1",
        "priority": "u=0, i",
        "sec-ch-ua": '"Google Chrome";v="135", "Not-A.Brand";v="8", "Chromium";v="135"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "none",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        # might want to use fake user agent
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
        "Cookie": "",
    }

    def __get_page(self, url):
        hash_url = hashlib.md5(url.encode()).hexdigest()
        cache_file = os.path.join(cache_dir, f"{hash_url}.html")
        os.makedirs(cache_dir, exist_ok=True)
        if os.path.exists(cache_file):
            modification_time = os.path.getmtime(cache_file)
            current_time = datetime.datetime.now()
            if (current_time - datetime.datetime.fromtimestamp(modification_time)).days <= cache_expire_days:
                with open(cache_file, "r") as file:
                    print(f"read {url} from cache")
                    return file.read()

        print(f"requesting {url} ...")
        time.sleep(request_pause_sec)
        response = self.session.get(url)
        response.raise_for_status()

        with open(cache_file, "w") as file:
            file.write(response.text)
        return response.text

    def __init__(self, city_name):
        self.city_name = city_name
        self.session = requests.Session()
        self.session.headers.update(self.__get_headers())
        self.city_url = self.__get_city_url()
        self.nodes = {}
        self.relationships = []
        self.transport_url = self.get_transport_url()
        self.transport_class = self.get_transport_class()

    def __get_headers(self):
        return self.__headers

    def parse(self):
        if self.city_url is None:
            return None, None

        for route_info in self.get_all_routes_info():
            route_name = route_info[0]
            route_url = route_info[2]

            self.__add_stops_and_routes(route_name, route_url)

        return self.nodes, self.relationships

    def __get_city_url(self):
        cities_url = self.load_cache(city_urls_file)
        if not cities_url:
            print("Cities url cache is expired or empty, lets fill it.")
            cities_url = self.parse_all_city_urls()
            self.save_cache(city_urls_file, cities_url)
            print("Cities url are saved in cache.")
        city_url = cities_url.get(self.city_name)
        if city_url is None:
            print("No such city in parsed data")
        return city_url

    def __add_stops_and_routes(self, route_name, route_url):
        timetable, successes_parse = self.get_timetable(route_url)
        if not successes_parse:
            return

        stop_coordinates = self.get_stop_coordinates(route_url)
        last_coordinate = Coordinate(city_avg_x_coordinate, city_avg_y_coordinate)
        previous_transport_stop_name = None
        previous_time_point = None

        for row in timetable:
            transport_stop_name = row["stopName"]
            time_point = row["timePoint"]
            coordinate = self.__get_filled_coordinate(stop_coordinates, transport_stop_name, last_coordinate)

            transport_stop = self.__update_or_add_stop(transport_stop_name, coordinate, route_name)

            if previous_transport_stop_name is not None:
                self.__add_route(
                    previous_transport_stop_name, transport_stop, previous_time_point, time_point, route_name
                )

            last_coordinate = coordinate
            previous_transport_stop_name = transport_stop
            previous_time_point = time_point

    def __get_filled_coordinate(self, stop_coordinates, stop_name, last_coordinate):
        coordinate = stop_coordinates.get(stop_name)
        if coordinate is None or not coordinate.is_defined():
            coordinate = Coordinate(last_coordinate.x, last_coordinate.y, True)
        return coordinate

    def __update_or_add_stop(self, transport_stop_name, coordinate, route_name):

        transport_stop_name, is_new_stop = self.__check_and_find_unique_stop(transport_stop_name, coordinate)

        if not is_new_stop:
            transport_stop = self.nodes.get(transport_stop_name)
            transport_stop["roteList"].append(route_name)
        else:
            transport_stop = {
                "name": transport_stop_name,
                "roteList": [route_name],
                "xCoordinate": coordinate.x,
                "yCoordinate": coordinate.y,
                "isCoordinateApproximate": coordinate.is_approximate,
            }
            self.nodes[transport_stop_name] = transport_stop
        return transport_stop

    def __check_and_find_unique_stop(self, transport_stop_name, coordinate):
        is_new_stop = True
        while self.nodes.get(transport_stop_name) is not None:
            transport_stop = self.nodes[transport_stop_name]
            old_coordinate = Coordinate(transport_stop["xCoordinate"], transport_stop["yCoordinate"])
            if self.are_stops_same(old_coordinate, coordinate):
                is_new_stop = False
                break
            transport_stop_name = self.increment_suffix(transport_stop_name)

        return transport_stop_name, is_new_stop

    def __add_route(self, start_stop, end_stop, start_time, end_time, route_name):
        if start_stop is not None and end_stop is not None:
            self.relationships.append(
                {
                    "startStop": start_stop["name"],
                    "endStop": end_stop["name"],
                    "name": start_stop["name"] + " -> " + end_stop["name"] + "; route_name: " + route_name,
                    "route": route_name,
                    "duration": self.calculate_duration(start_time, end_time),
                }
            )

    def get_all_routes_info(self):
        if self.city_url is None:
            return []

        full_url = site_url + self.city_url + self.transport_url

        response_html = self.__get_page(full_url)

        soup = BeautifulSoup(response_html, "html.parser")

        transport_list = []

        bus_items = soup.find_all("a", class_=self.transport_class)
        for item in bus_items:
            transport_number = item.text.strip()
            transport_route = item.find("span").text.strip()
            href_link = item["href"]
            transport_list.append([transport_number, transport_route, href_link])
        return transport_list

    def get_timetable(self, route_url):
        (timetable1, successes_parse1) = self.get_one_direction_timetable(route_url, timetable_forward_url)
        (timetable2, successes_parse2) = self.get_one_direction_timetable(route_url, timetable_backward_url)
        if successes_parse1 and successes_parse2:
            return timetable1 + timetable2, True
        return None, False

    def get_one_direction_timetable(self, route_url, timetable_url):
        full_url = site_url + route_url + timetable_url

        response_html = self.__get_page(full_url)
        soup = BeautifulSoup(response_html, "html.parser")

        stop_times = []
        for stop_div in soup.find_all("div", class_="bus-stop"):
            name = stop_div.find("a").text.strip()
            time_point = stop_div.find_next_sibling("div", class_="col-xs-12").find("span")
            if time_point is not None:
                parsed_time_point = time_point.text.strip()
                if parsed_time_point[len(parsed_time_point) - 1] == "K":
                    parsed_time_point = parsed_time_point[:-1]
            else:
                return None, False
            clean_name = re.sub(r"\d+\) ", "", name)
            stop_times.append({"stopName": clean_name, "timePoint": parsed_time_point})
        return stop_times, True

    def get_route(self, route_url):
        full_url = site_url + route_url + map_url
        response_html = self.__get_page(full_url)
        soup = BeautifulSoup(response_html, "html.parser")

        script_tags = soup.find_all("script", type="text/javascript")
        script_tag = None
        for tag in script_tags:
            if "drawMap" in tag.text:
                script_tag = tag
                break

        if script_tag is None:
            print("No script tag found with drawMap")
            return []
        script_text = script_tag.text.strip().removeprefix("drawMap(\n").removesuffix(");")
        regexp = r'{\"1\":\[\[.*\]\]\}'
        match = re.search(regexp, script_text)
        if match:
            script_text = match.group(0)
        else:
            print("No match found in script text")
            return []

        coords = json.loads(script_text)
        return coords["1"]

    def get_stop_coordinates(self, route_url):
        full_url = site_url + route_url + map_url
        response_html = self.__get_page(full_url)
        soup = BeautifulSoup(response_html, "html.parser")

        script_tags = soup.find_all("script", type="text/javascript")
        script_tag = None

        for tag in script_tags:
            if "drawMap" in tag.text:
                script_tag = tag
                break

        if script_tag is None:
            print("No script tag found with drawMap")
            return {}

        script_text = script_tag.text
        coordinates = self.extract_coordinates(script_text)

        return coordinates

    def extract_coordinates(self, script_text):
        # TODO: that json which we can parse..
        matches = re.findall(r'{"name":\s*"(.*?)",\s*"lat":\s*(-?\d+\.?\d*),?\s*"long":\s*(-?\d+\.?\d*)?}', script_text)

        coordinates = {}
        for match in matches:
            name = match[0].replace("\\", "")
            # `match` contains latitude and longitude which equals to y and x coordinates
            x = float(match[2])
            y = float(match[1])
            coordinates[name] = Coordinate(x, y)

        return coordinates

    def load_cache(self, cache_file):
        if os.path.exists(cache_file):
            modification_time = os.path.getmtime(cache_file)
            current_time = datetime.datetime.now()
            if (current_time - datetime.datetime.fromtimestamp(modification_time)).days <= cache_expire_days:
                with open(cache_file, "r") as file:
                    return json.load(file)
        return {}

    def save_cache(self, cache_file, cache_data):
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w") as file:
            json.dump(cache_data, file)

    def parse_all_city_urls(self):
        url = "https://kudikina.ru/"
        html_content = self.__get_page(url)

        soup = BeautifulSoup(html_content, "html.parser")
        cities = {}

        for li in soup.find_all("ul", class_="list-unstyled cities block-regions"):
            for region in li.find_all("a"):
                region_name = region.find("span", class_="city-name").text.strip()
                region_href = region["href"]
                region_html_content = self.__get_page(url[:-1] + region_href)
                region_soup = BeautifulSoup(region_html_content, "html.parser")
                city_list = region_soup.find_all("ul", class_="list-unstyled cities")

                if not city_list:
                    cities[region_name] = region_href
                    print(region_href + " Was parsed")
                    continue
                region_cities = city_list[0].find_all("a")
                for city in region_cities:
                    city_name = city.find("span", class_="city-name").text.strip()
                    city_href = city["href"]
                    cities[city_name] = city_href
                    print(city_href + " Was parsed")
        return cities

    def calculate_duration(self, start_stop, end_stop):
        # TODO: test with 23:55 and :05
        start_time = self.parse_time(start_stop)
        end_time = self.parse_time(end_stop)

        if start_time is None or end_time is None:
            return None

        duration = end_time - start_time
        return duration.total_seconds()

    def parse_time(self, time_str):
        if not time_str:
            return None

        time_str = time_str.replace(" ", "")
        if len(time_str) == 3 and time_str[0] == ":":
            # e.g. ":30"
            time_str = "00" + time_str
        try:
            return datetime.datetime.strptime(time_str, "%H:%M")
        except ValueError:
            return None

    def are_stops_same(self, coord1, coord2, tolerance=0.005):
        distance = math.dist(coord1.get_xy(), coord2.get_xy())
        return abs(distance) < tolerance

    def increment_suffix(self, name):
        if name and name[-1].isdigit():
            index = len(name) - 1
            while index >= 0 and name[index].isdigit():
                index -= 1
            number = int(name[index + 1 :]) + 1
            return f"{name[:index + 1]}{number}"

        return f"{name} 1"

    @abstractmethod
    def get_transport_class(self):
        pass

    @abstractmethod
    def get_transport_url(self):
        pass


class BusGraphParser(AbstractTransportGraphParser):
    def get_transport_class(self):
        return "bus-item bus-icon"

    def get_transport_url(self):
        return "bus/"


class TrolleyGraphParser(AbstractTransportGraphParser):
    def get_transport_url(self):
        return "trolley/"

    def get_transport_class(self):
        return "bus-item trolley-icon"


class BusGraphParser(AbstractTransportGraphParser):

    def get_transport_url(self):
        return "bus/"

    def get_transport_class(self):
        return "bus-item bus-icon"


class MiniBusGraphParser(AbstractTransportGraphParser):
    def get_transport_url(self):
        return "mtaxi/"

    def get_transport_class(self):
        return "bus-item mtaxi-icon"


class TramGraphParser(AbstractTransportGraphParser):
    def get_transport_url(self):
        return "tram/"

    def get_transport_class(self):
        return "bus-item tram-icon"


class Coordinate:
    def __init__(self, x=None, y=None, is_approximate=False):
        self.x = x
        self.y = y
        self.is_approximate = is_approximate

    def __str__(self):
        return f"({self.x}, {self.y})"

    def is_defined(self):
        if self.x is None or self.y is None:
            return False
        else:
            return True

    def get_xy(self):
        return [self.x, self.y]
