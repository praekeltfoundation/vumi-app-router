# -*- test-case-name: vxapprouter.tests.test_router -*-

from vumi.config import ConfigDict, ConfigList, ConfigInt, ConfigText
from vumi.dispatchers.endpoint_dispatchers import Dispatcher


class ApplicationDispatcherConfig(Dispatcher.CONFIG_CLASS):

    # Static configuration
    session_expiry = ConfigInt(
        ("Maximum amount of time in seconds to keep session data around. "
         "Defaults to 5 minutes."),
        default=5 * 60, static=True)

    # Dynamic, per-message configuration
    menu_title = ConfigDict(
        "Content for the menu title",
        default={'content': "Please select a choice."})
    entries = ConfigList(
        "A list of application endpoints and associated labels",
        default=[])
    invalid_input_message = ConfigText(
        "Prompt to display when warning about an invalid choice",
        default=("That is an incorrect choice. Please enter the number "
                 "of the menu item you wish to choose.\n\n 1) Try Again"))
    error_message = ConfigText(
        ("Prompt to display when a configuration change invalidates "
         "an active session."),
        default=("Oops! We experienced a temporary error. "
                 "Please try and dial the line again."))


class ApplicationDispatcher(Dispatcher):
    CONFIG_CLASS = ApplicationDispatcherConfig
    worker_name = None

    def process_inbound(self, config, msg, connector_name):
        target = self.find_target(config, msg, connector_name)
        if target is None:
            return
        return self.publish_inbound(msg, target[0], target[1])

    def process_outbound(self, config, msg, connector_name):
        target = self.find_target(config, msg, connector_name)
        if target is None:
            return
        return self.publish_outbound(msg, target[0], target[1])

    def process_event(self, config, event, connector_name):
        target = self.find_target(config, event, connector_name)
        if target is None:
            return
        return self.publish_event(event, target[0], target[1])
