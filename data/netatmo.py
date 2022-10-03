import argparse
import email
import socket
import sys
import time
import os
import configparser
import json
import requests
from lxml import html
import urllib.parse
import logging


def main(args):
    """
    Check if running in debug mode
    """
    if args.debug:
        import debugpy
        print("running in debug mode - waiting for debugger connection on {0}:{1}".format(args.debugip, args.debugport))
        debugpy.listen((args.debugip, args.debugport))
        debugpy.wait_for_client()

    """
    # Parse PlugIn config file
    """
    if not os.path.exists(args.configfile):
        logging.critical("Plugin configuration file missing {0}".format(args.configfile))
        sys.exit(-1)

    pluginconfig = configparser.ConfigParser()
    pluginconfig.read(args.configfile)
    username = pluginconfig.get('NETATMO', 'USER_EMAIL')
    password = pluginconfig.get('NETATMO', 'PASSWORD')
    client_id = pluginconfig.get('NETATMO', 'CLIENT_ID')
    client_secret = pluginconfig.get('NETATMO', 'CLIENT_SECRET')
    enabled = pluginconfig.get('NETATMO', 'ENABLED')
    device_id=pluginconfig.get('NETATMO', 'DEVICE_ID')
    localtime = pluginconfig.get('NETATMO', 'ENABLED')
    miniservername = pluginconfig.get('NETATMO', 'MINISERVER')
    virtualUDPPort = int(pluginconfig.get('NETATMO', 'UDPPORT'))

    """
    transistion from general.cfg to general.json
    """
    if miniservername.startswith("MINISERVER"):
        miniserverID = miniservername.replace("MINISERVER", "")

    else:
        miniserverID = miniservername
        miniservername = "MINISERVER{0}".format(miniserverID)

    """
    Check if general.json exists and Loxberry version > 2.2
    """
    lbsConfigGeneralJSON = os.path.join(Config.Loxberry("LBSCONFIG"), "general.json")
    lbsConfigGeneralCFG = os.path.join(Config.Loxberry("LBSCONFIG"), "general.cfg")

    if not os.path.exists(lbsConfigGeneralJSON):
        logging.warning("gerneral.json missing in path {0}".format(lbsConfigGeneralJSON))
        logging.warning("trying general.cfg instead {0}".format(lbsConfigGeneralCFG))

        if not os.path.exists(lbsConfigGeneralCFG):
            logging.critical("general.cfg not found in path {0}".format(lbsConfigGeneralCFG))
            sys.exit(-1)

        """
        general.cfg (legacy configuration file)
        """
        logging.info("using system configuration file {0}/general.cfg".format(Config.Loxberry("LBSCONFIG")))
        loxberryconfig = configparser.ConfigParser()
        loxberryconfig.read("{0}/general.cfg".format(Config.Loxberry("LBSCONFIG")))
        miniserverIP = loxberryconfig.get(miniservername, 'IPADDRESS')

    else:
        with open(lbsConfigGeneralJSON, "r") as lbsConfigGeneralJSONHandle:
            logging.info("using system configuration file {0}/general.json".format(Config.Loxberry("LBSCONFIG")))
            data = json.load(lbsConfigGeneralJSONHandle)

        # check if miniserver from plugin config exists in general.json
        if not miniserverID in data["Miniserver"].keys():
            logging.critical("Miniserver with id {0} not found general.json - please check plugin configuration".format(miniserverID))
            sys.exit(-1)

        miniserverIP = data["Miniserver"][miniserverID]["Ipaddress"]
        logging.info("Miniserver ip address: {0}".format(miniserverIP))

    """
    exit if PlugIn is not enabled
    """
    if enabled != "1":
        logging.warning("Plugin is not enabled in configuration - exiting")
        sys.exit(-1)

    """
    start new request session
    """
    session = requests.Session()

    """
    set User-Agent to emulate Windows 10 / IE 11
    """
    session.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64; Trident/7.0; rv:11.0) like Gecko'}

    """
    fetch token with client credentials
    """
    url = "https://api.netatmo.com/oauth2/token"
    payload = {
       "client_id" : client_id,
       "client_secret" : client_secret,
       "grant_type": "password",
       "username" : username,
       "password" : password
    }

    req = session.post(url, payload,
     headers={'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'}, json = payload)

    if req.status_code != 200:
        logging.error("Unable to contact https://api.netatmo.com/oauth2/token")
        logging.critical("Error: {0}".format(req.status_code))
        sys.exit(-1)
    else:
        logging.info("Successfully got access data from https://api.netatmo.com/oauth2/token")

    csrf = json.loads(req.text)
    access_token = csrf["access_token"]
    # refresh_token = csrf["refresh_token"] not sure if needed

    header = { "accept": "application/json" , "Authorization" : "Bearer " + access_token}

    req = session.get("https://api.netatmo.com/api/getstationsdata?" + device_id, headers=header)

    """
    convert the response into json
    """
    netatmodata = json.loads(req.text)

    """
    Loop for each station and module
    """
    for device in netatmodata["body"]["devices"]:

        """
        Get WiFi Signal
        """
        value = "{0}.{1}.{2}={3}".format(device["home_name"], device["module_name"], "wifi_status",
                                         str(device["wifi_status"]))

        # send udp datagram
        sendudp(value, miniserverIP, virtualUDPPort)
        logging.info(value)

        """
        Get devicereachable (a.k.a. offline)
        """
        value = "{0}.{1}.{2}={3}".format(device["home_name"], device["module_name"], "reachable", str(int(device["reachable"])))

        # send udp datagram
        sendudp(value, miniserverIP, virtualUDPPort)
        logging.info(value)

        # only process station with data (a.k.a. ignore offline stations)
        if 'dashboard_data' in device.keys():

            # Loop for each sensor in station
            for sensor in device["dashboard_data"].keys():

                if (sensor.lower() == "time_utc") or (sensor.lower() == "date_min_temp") or (
                        sensor.lower() == "date_max_temp") or (sensor.lower() == "date_max_wind_str"):

                    # Calculate offset based on 01.01.2009
                    loxBaseEpoch = 1230768000

                    # Get Time from Sensor
                    sensorTime = device["dashboard_data"][sensor]

                    # Convert time to localtime if enabled
                    if localtime == "1":
                        sensorLocalTime = time.localtime(sensorTime)

                        sensorTime = sensorTime + sensorLocalTime.tm_gmtoff

                    # Subtract time / date offset
                    loxSensorTime = sensorTime - loxBaseEpoch

                    value = "{0}.{1}.{2}={3}".format(device["home_name"], device["module_name"], sensor,
                                                     loxSensorTime)

                # convert trend values down,up,stable into -1, 1 and 0
                elif (sensor.lower() == "pressure_trend") or (sensor.lower() == "temp_trend"):

                    if device["dashboard_data"][sensor] == "up":
                        value = "{0}.{1}.{2}={3}".format(device["home_name"], device["module_name"], sensor, "1")

                    elif device["dashboard_data"][sensor] == "down":
                        value = "{0}.{1}.{2}={3}".format(device["home_name"], device["module_name"], sensor, "-1")

                    elif device["dashboard_data"][sensor] == "stable":
                        value = "{0}.{1}.{2}={3}".format(device["home_name"], device["module_name"], sensor, "0")

                    else:
                        value = "{0}.{1}.{2}={3}".format(device["home_name"], device["module_name"], sensor,
                                                         str((device["dashboard_data"][sensor])))

                else:
                    value = "{0}.{1}.{2}={3}".format(device["home_name"], device["module_name"], sensor,
                                                     str((device["dashboard_data"][sensor])))

                # send udp datagram
                sendudp(value, miniserverIP, virtualUDPPort)
                logging.info(value)

            # handle base station without any modules
            if 'modules' in device.keys():

                # loop for each module
                for module in device["modules"]:

                    """
                    Get battery level
                    """
                    value = "{0}.{1}.{2}={3}".format(device["home_name"], module["module_name"], "battery_percent",
                                                     str(module["battery_percent"]))

                    # send udp datagram
                    sendudp(value, miniserverIP, virtualUDPPort)
                    logging.info(value)

                    """
                    Get RF signal quality
                    """
                    value = "{0}.{1}.{2}={3}".format(device["home_name"], module["module_name"], "rf_status",
                                                     str(module["rf_status"]))

                    # send udp datagram
                    sendudp(value, miniserverIP, virtualUDPPort)
                    logging.info(value)

                    """
                    Get devicereachable (a.k.a. offline)
                    """
                    value = "{0}.{1}.{2}={3}".format(device["home_name"], module["module_name"], "reachable", str(int(module["reachable"])))

                    # send udp datagram
                    sendudp(value, miniserverIP, virtualUDPPort)
                    logging.info(value)

                    # only process modules with data (a.k.a. ignore offline modules)
                    if 'dashboard_data' in module.keys():

                        # Loop for each sensor in module
                        for sensor in module["dashboard_data"]:

                            if (sensor.lower() == "time_utc") or (sensor.lower() == "date_min_temp") or (
                                    sensor.lower() == "date_max_temp") or (sensor.lower() == "date_max_wind_str"):

                                # Calculate offset based on 01.01.2009
                                loxBaseEpoch = 1230768000

                                # Get Time from Sensor
                                sensorTime = module["dashboard_data"][sensor]

                                # Convert time to localtime if enabled
                                if localtime == "1":
                                    sensorLocalTime = time.localtime(sensorTime)

                                    sensorTime = sensorTime + sensorLocalTime.tm_gmtoff

                                # Subtract time / date offset
                                loxSensorTime = sensorTime - loxBaseEpoch

                                value = "{0}.{1}.{2}={3}".format(device["home_name"], module["module_name"], sensor,
                                                                 loxSensorTime)

                            # convert trend values down,up,stable into -1, 1 and 0
                            elif (sensor.lower() == "pressure_trend") or (sensor.lower() == "temp_trend"):

                                if module["dashboard_data"][sensor] == "up":
                                    value = "{0}.{1}.{2}={3}".format(device["home_name"], module["module_name"], sensor, "1")

                                elif module["dashboard_data"][sensor] == "down":
                                    value = "{0}.{1}.{2}={3}".format(device["home_name"], module["module_name"], sensor, "-1")

                                elif module["dashboard_data"][sensor] == "stable":
                                    value = "{0}.{1}.{2}={3}".format(device["home_name"], module["module_name"], sensor, "0")

                                else:
                                    value = "{0}.{1}.{2}={3}".format(device["home_name"], module["module_name"], sensor,
                                                                     str((module["dashboard_data"][sensor])))

                            else:
                                value = "{0}.{1}.{2}={3}".format(device["home_name"], module["module_name"], sensor,
                                                                 str(module["dashboard_data"][sensor]))

                            # send udp datagram
                            sendudp(value, miniserverIP, virtualUDPPort)
                            logging.info(value)

    # exit with errorlevel 0
    sys.exit(0)

# _______________________________________________________________________________________


def sendudp(data, destip, destport):
    # start a new connection udp connection
    # connection = socket.socket(socket.AF_INET,     # Internet
                            #    socket.SOCK_DGRAM)  # UDP

    # send udp datagram
    # res = connection.sendto(data.encode(), (destip, destport))

    # close udp connection
    # connection.close()
    print(data)
    print("\n")
    # check if all bytes in resultstr were sent
    # if res != data.encode().__len__():
    #     logging.error("Sent bytes do not match - expected {0} : got {1}".format(data.__len__(), res))
    #     logging.critical("Packet-Payload {0}".format(data))
    #     sys.exit(-1)

# _______________________________________________________________________________________


class Config:
    __loxberry = {
        "LBSCONFIG": os.getenv("LBSCONFIG", os.getcwd()),
    }

    @staticmethod
    def Loxberry(name):
        return Config.__loxberry[name]

# _______________________________________________________________________________________


# parse args and call main function
print('Number of arguments:', len(sys.argv), 'arguments.')
print('Argument List:', str(sys.argv))

if __name__ == "__main__":
    """
    Parse commandline arguments
    """
    parser = argparse.ArgumentParser(description="Loxberry Netatmo-Weather Plugin. More information can be found on Github site https://github.com/michaelmiklis/loxberry-plugin-netatmo-weather")

    debugroup = parser.add_argument_group("debug")

    debugroup.add_argument("--debug",
                           dest="debug",
                           default=False,
                           action="store_true",
                           help="enable debug mode")

    debugroup.add_argument("--debugip",
                           dest="debugip",
                           default=socket.gethostbyname(socket.gethostname()),
                           action="store",
                           help="Local IP address to listen for debugger connections (default={0})".format(socket.gethostbyname(socket.gethostname())))

    debugroup.add_argument("--debugport",
                           dest="debugport",
                           default=5678,
                           action="store",
                           help="TCP port to listen for debugger connections (default=5678)")

    loggroup = parser.add_argument_group("log")

    loggroup.add_argument("--logfile",
                          dest="logfile",
                          default="netatmo-weather.log",
                          type=str,
                          action="store",
                          help="specifies logfile path")

    loggroup = parser.add_argument_group("config")

    loggroup.add_argument("--configfile",
                          dest="configfile",
                          default="netatmo.cfg",
                          type=str,
                          action="store",
                          help="specifies plugin configuration file path")

    debugroup = parser.add_argument_group("apibody")

    debugroup.add_argument("--apibody",
                           dest="apibody",
                           default=False,
                           action="store_true",
                           help="output JSON response from Netatmo API")

    args = parser.parse_args()

    """
    # logging configuration
    """
    logging.getLogger().setLevel(logging.DEBUG)
    logging.basicConfig(filename=args.logfile,
                        filemode='w',
                        level=logging.DEBUG,
                        format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S',)

    # define a Handler which writes INFO messages or higher to the sys.stderr
    console = logging.StreamHandler()
    console.setLevel(logging.NOTSET)
    # add the handler to the root logger
    logging.getLogger('').addHandler(console)
    logging.info("using plugin log file {0}".format(args.logfile))

    """
    call main function
    """
    try:
        main(args)
    except Exception as e:
        logging.critical(e, exc_info=True)
