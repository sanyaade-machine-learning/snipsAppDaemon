import paho.mqtt.client as mqtt
import asyncio
import json
import ssl
from websocket import create_connection
from pkg_resources import parse_version
from sseclient import SSEClient
import traceback
import aiohttp
import utils as utils #

class SnipsPlugin:

    def __init__(self, ad, name, logger, error, loglevel, args):

        self.AD = ad
        self.logger = logger
        self.error = error
        self.stopping = False
        self.config = args
        self.loglevel = loglevel
        self.ws = None
        self.reading_messages = False
        self.name = name

        self.log("INFO", "Snips HASS Plugin Initializing")

        self.name = name

        if "namespace" in args:
            self.namespace = args["namespace"]
        else:
            self.namespace = "snips"

        if "verbose" in args:
            self.verbose = args["verbose"]
        else:
            self.verbose = False

        if "ha_key" in args:
            self.ha_key = args["ha_key"]
        else:
            self.ha_key = ""

        if "ha_url" in args:
            self.ha_url = args["ha_url"]
        else:
            self.log("WARN", "ha_url not found in HASS configuration - module not initialized")

        if "cert_path" in args:
            self.cert_path = args["cert_path"]
        else:
            self.cert_path = None

        if "timeout" in args:
            self.timeout = args["timeout"]
        else:
            self.timeout = None

        if "cert_verify" in args:
            self.cert_verify = args["cert_verify"]
        else:
            self.cert_verify = True

        if "commtype" in args:
            self.commtype = args["commtype"]
        else:
            self.commtype = "WS"

        #
        # Set up HTTP Client
        #
        conn = aiohttp.TCPConnector()
        self.session = aiohttp.ClientSession(connector=conn)

        #
        # Set up MQTT Client and Connections
        #
        self.mqtt_client_host = args.get('mqtt_client_host', '127.0.0.1')
        self.mqtt_client_port = args.get('mqtt_client_port', 1883)
        self.mqtt_client_topics = args.get('mqtt_client_topics', ['#'])
        self.mqtt_client_user = args.get('mqtt_client_user', None)
        self.mqtt_client_password = args.get('mqtt_client_password', None)

        self.mqtt_client_timeout = args.get('mqtt_client_timeout', 60)

        self.mqtt_client = mqtt.Client(client_id="HASS", clean_session=True)
        self.mqtt_client.on_connect = self.mqtt_on_connect
        self.mqtt_client.on_message = self.mqtt_on_message
        #self.mqtt_client.on_publish = self.mqtt_on_publish
        #self.mqtt_client.on_subscribe = self.mqtt_on_subscribe
        #self.mqtt_client.on_disconnect = self.mqtt_on_disconnect

        if self.mqtt_client_user:
            self.mqtt_client.username_pw_set(self.mqtt_client_user, password=self.mqtt_client_password)

        self.log("INFO", "MQTT Connecting to: {}:{}".format(self.mqtt_client_host,self.mqtt_client_port))

        
        self.mqtt_client.connect_async(self.mqtt_client_host, self.mqtt_client_port, self.mqtt_client_timeout)
        self.mqtt_client.loop_start()
        
        self.log("INFO", "Snips HASS Plugin initialization complete")

    def log(self, level, message):
        self.AD.log(level, "{}: {}".format(self.name, message))

    def verbose_log(self, text):
        if self.verbose:
            self.log("INFO", text)
    
    def mqtt_on_subscribe(self, mosq, obj, mid, granted_qos):
        self.log("INFO", "MQTT on_subscribe: {} | {} | {} | {}".format(mosq, obj, mid, granted_qos))

    def mqtt_on_disconnect(self, client, userdata, rc):
        self.log("INFO", "MQTT on_disconnect: {}".format(rc))


    def mqtt_on_connect(self, client, userdata, flags, rc):
        for topic in self.mqtt_client_topics:
            #self.log("INFO", "MQTT on_connect: subscribed to: {}".format(topic))
            self.mqtt_client.subscribe(topic, 0)

    def mqtt_on_message(self, client, userdata, msg):
        if msg.topic is not None and msg.topic.startswith("hermes/intent/") and msg.payload:
            #self.AD.log('DEBUG', "MQTT payload: {}".format(msg.payload))
            data = json.loads(msg.payload)
            if 'intent' in data and 'intentName' in data['intent']:
                snips_intent_name = data['intent']['intentName'].split(':')[-1].split(":")[-1]
                self.AD.state_update(self.namespace, {'event_type': snips_intent_name, 'data': {'topic': msg.topic, 'payload': msg.payload.decode('utf-8')}})

        #self.AD.state_update(self.namespace,
        #    {'event_type': 'MQTT_MESSAGE', 'data': {'topic': msg.topic,
        #            'payload': msg.payload.decode('utf-8')}})

    def mqtt_on_publish(self, client, userdata, msg):
        self.log("DEBUG", "MQTT on_publish: {}".format(msg.payload))

    def stop(self):
        self.log("INFO", "Stopping MQTT")
        self.mqtt_client.loop_stop()
        self.verbose_log("*** Stopping ***")
        self.stopping = True
        if self.ws is not None:
            self.ws.close()

    #
    # Get initial state
    #

    async def get_complete_state(self):
        hass_state = await self.get_hass_state()
        states = {}
        for state in hass_state:
            states[state["entity_id"]] = state
        self.log("DEBUG", "Got state")
        self.verbose_log("*** Sending Complete State: {} ***".format(hass_state))
        return states

    #
    # Get HASS Metadata
    #

    async def get_metadata(self):
        return await self.get_hass_config()

    #
    # Handle state updates
    #

    async def get_updates(self):

        _id = 0

        already_notified = False
        first_time = True
        while not self.stopping:
            _id += 1
            disconnected_event = False
            try:

                if parse_version(utils.__version__) < parse_version('0.34') or self.commtype == "SSE":
                    #
                    # Older version of HA - connect using SSEClient
                    #
                    if self.commtype == "SSE":
                        self.log("WARNING", "Using SSE - use of SSE is deprecated and will be removed in a future version")
                    else:
                        self.log(
                            "INFO",
                            "Home Assistant version < 0.34.0 - "
                            "falling back to SSE"
                        )
                    headers = {'x-ha-access': self.ha_key}
                    if self.timeout is None:
                        messages = SSEClient(
                            "{}/api/stream".format(self.ha_url),
                            verify=False, headers=headers, retry=3000
                        )
                        self.log(
                            "INFO",
                            "Connected to Home Assistant".format(self.timeout)
                        )
                    else:
                        messages = SSEClient(
                            "{}/api/stream".format(self.ha_url),
                            verify=False, headers=headers, retry=3000,
                            timeout=int(self.timeout)
                        )
                        self.log(
                            "INFO",
                            "Connected to Home Assistant with timeout = {}".format(
                                self.timeout
                            )
                        )
                    self.reading_messages = True
                    #
                    # Fire HA_STARTED Events
                    #
                    await self.AD.notify_plugin_started(self.namespace, first_time)

                    already_notified = False

                    while not self.stopping:
                        msg = await utils.run_in_executor(self.AD.loop, self.AD.executor, messages.__next__)
                        if msg.data != "ping":
                            self.AD.state_update(self.namespace, json.loads(msg.data))
                    self.reading_messages = False
                else:
                    #
                    # Connect to websocket interface
                    #
                    url = self.ha_url
                    if url.startswith('https://'):
                        url = url.replace('https', 'wss', 1)
                    elif url.startswith('http://'):
                        url = url.replace('http', 'ws', 1)

                    sslopt = {'cert_reqs': ssl.CERT_NONE}
                    if self.cert_path:
                        sslopt['ca_certs'] = self.cert_path
                    self.ws = create_connection(
                        "{}/api/websocket".format(url), sslopt=sslopt
                    )
                    res = await utils.run_in_executor(self.AD.loop, self.AD.executor, self.ws.recv)
                    result = json.loads(res)
                    self.log("INFO",
                              "Connected to Home Assistant {}".format(
                                  result["ha_version"]))
                    #
                    # Check if auth required, if so send password
                    #
                    if result["type"] == "auth_required":
                        auth = json.dumps({
                            "type": "auth",
                            "api_password": self.ha_key
                        })
                        await utils.run_in_executor(self.AD.loop, self.AD.executor, self.ws.send, auth)
                        result = json.loads(self.ws.recv())
                        if result["type"] != "auth_ok":
                            self.log("WARNING",
                                      "Error in authentication")
                            raise ValueError("Error in authentication")
                    #
                    # Subscribe to event stream
                    #
                    sub = json.dumps({
                        "id": _id,
                        "type": "subscribe_events"
                    })
                    await utils.run_in_executor(self.AD.loop, self.AD.executor, self.ws.send, sub)
                    result = json.loads(self.ws.recv())
                    if not (result["id"] == _id and result["type"] == "result" and
                                    result["success"] is True):
                        self.log(
                            "WARNING",
                            "Unable to subscribe to HA events, id = {}".format(_id)
                        )
                        self.log("WARNING", result)
                        raise ValueError("Error subscribing to HA Events")

                    #
                    # Loop forever consuming events
                    #
                    self.reading_messages = True
                    #
                    # Fire HA_STARTED Events
                    #
                    await self.AD.notify_plugin_started(self.namespace, first_time)

                    already_notified = False

                    while not self.stopping:
                        ret = await utils.run_in_executor(self.AD.loop, self.AD.executor, self.ws.recv)
                        result = json.loads(ret)

                        if not (result["id"] == _id and result["type"] == "event"):
                            self.log(
                                "WARNING",
                                "Unexpected result from Home Assistant, "
                                "id = {}".format(_id)
                            )
                            self.log("WARNING", result)
                            raise ValueError(
                                "Unexpected result from Home Assistant"
                            )

                        self.AD.state_update(self.namespace, result["event"])
                    self.reading_messages = False

            except:
                self.reading_messages = False
                first_time = False
                if not already_notified:
                    self.AD.notify_plugin_stopped(self.namespace)
                    already_notified = True
                if not self.stopping:
                    if disconnected_event == False:
                        self.AD.state_update(self.namespace, {"event_type": "ha_disconnected", "data": {}})
                        disconnected_event = True
                    self.log(
                        "WARNING",
                        "Disconnected from Home Assistant, retrying in 5 seconds"
                    )
                    if self.loglevel == "DEBUG":
                        self.log( "WARNING", '-' * 60)
                        self.log( "WARNING", "Unexpected error:")
                        self.log("WARNING", '-' * 60)
                        self.log( "WARNING", traceback.format_exc())
                        self.log( "WARNING", '-' * 60)
                    await asyncio.sleep(5)

        self.log("INFO", "Disconnecting from Home Assistant")

    def get_namespace(self):
        return self.namespace

    #
    # Utility functions
    #

    def utility(self):
       return None


    def active(self):
        return self.reading_messages

    #
    # Home Assistant Interactions
    #

    async def get_hass_state(self, entity_id=None):
        if self.ha_key != "":
            headers = {'x-ha-access': self.ha_key}
        else:
            headers = {}
        if entity_id is None:
            apiurl = "{}/api/states".format(self.ha_url)
        else:
            apiurl = "{}/api/states/{}".format(self.ha_url, entity_id)
        self.log("DEBUG", "get_ha_state: url is {}".format(apiurl))
        r = await self.session.get(apiurl, headers=headers, verify_ssl=self.cert_verify)
        r.raise_for_status()
        return await r.json()

    async def get_hass_config(self):
        self.log("DEBUG", "get_ha_config()")
        if self.ha_key != "":
            headers = {'x-ha-access': self.ha_key}
        else:
            headers = {}
        apiurl = "{}/api/config".format(self.ha_url)
        self.log("DEBUG", "get_ha_config: url is {}".format(apiurl))
        r = await self.session.get(apiurl, headers=headers, verify_ssl=self.cert_verify)
        r.raise_for_status()
        return await r.json()

    @staticmethod
    def _check_service(service):
        if service.find("/") == -1:
            raise ValueError("Invalid Service Name: {}".format(service))

    async def call_service(self, service, **kwargs):
        self._check_service(service)
        d, s = service.split("/")
        self.log(
            "DEBUG",
            "call_service: {}/{}, {}".format(d, s, kwargs)
        )
        if self.ha_key != "":
            headers = {'x-ha-access': self.ha_key}
        else:
            headers = {}
        apiurl = "{}/api/services/{}/{}".format(self.ha_url, d, s)

        #async with aiohttp.ClientSession() as client:
        #    #async with client.post(apiurl, headers=headers, json=kwargs, verify=self.cert_path) as resp:
        #    async with client.post(apiurl, headers=headers, json=kwargs, verify_ssl=False) as resp:
        #        assert resp.status == 200
        #        return await resp.json()

        r = await self.session.post(apiurl, headers=headers, json=kwargs, verify_ssl=self.cert_verify)
        r.raise_for_status()
        return r.json()