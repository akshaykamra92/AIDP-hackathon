"""API wiring smoke checks; skipped when FastAPI is not installed."""

import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "FastAPI is not installed")
class ApiContractTests(unittest.TestCase):
    def setUp(self):
        from app.main import create_app

        class Repository:
            def health(self):
                return True

        class Service:
            repository = Repository()

            def ask(self, question):
                return {"tier": "EXPLORATORY", "question": question, "message": "Fixture refusal"}

            def certification_request(self, question):
                return {"request_id": 7, "status": "OPEN"}

            def demo_turnover_comparison(self):
                return {"certified": {"value": 1.89}, "ad_hoc": {"value": 2.33}}

        self.app = create_app(Service())

    def _endpoint(self, path, method):
        for route in self.app.routes:
            if getattr(route, "path", None) == path and method in (getattr(route, "methods", set()) or set()):
                return route.endpoint
        self.fail("Missing route %s %s" % (method, path))

    def test_route_contract(self):
        from app.main import AskRequest, CertificationRequest

        self.assertEqual("ok", self._endpoint("/health", "GET")()["status"])
        self.assertEqual("EXPLORATORY", self._endpoint("/ask", "POST")(AskRequest(question="Burnout rate"))["tier"])
        self.assertEqual(
            7,
            self._endpoint("/certification-request", "POST")(
                CertificationRequest(question="Burnout rate")
            )["request_id"],
        )
        self.assertEqual(
            1.89,
            self._endpoint("/demo/turnover-comparison", "GET")()["certified"]["value"],
        )
        self.assertTrue(any(getattr(route, "path", None) == "/static" for route in self.app.routes))


if __name__ == "__main__":
    unittest.main()
