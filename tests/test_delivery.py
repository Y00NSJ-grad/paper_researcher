import unittest

import httpx

from radar.delivery import SlackWebhookDelivery


class DeliveryTest(unittest.TestCase):
    def test_slack_error_does_not_expose_webhook_url(self):
        secret_url = "https://hooks.slack.test/services/secret-value"
        delivery = SlackWebhookDelivery(secret_url)
        delivery.client = httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        )

        with self.assertRaisesRegex(RuntimeError, "Slack webhook delivery failed") as raised:
            delivery.send("report")

        self.assertNotIn(secret_url, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
