
#import plugins.snips.snipsapi as hass
import plugins.mqtt.mqttapi as mqtt


#
# Hello World App
#
# Args:
#

class HelloWorld(mqtt.Mqtt):

  def initialize(self):
    self.log("Hello from AppDaemon")
    self.log("You are now ready to run Apps!")
    self.listen_event(self.mqtt_message, "adminRequests")
    #self.listen_event(self.mqtt_message, "MQTT_MESSAGE", intent = 'adminRequests')

  def mqtt_message(self, event_name, data, *args, **kwargs):
    self.log("mqtt_message: {} | {}".format(event_name, data))