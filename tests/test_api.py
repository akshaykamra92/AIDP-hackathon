"""API wiring smoke checks; skipped when FastAPI is not installed."""

import importlib.util
import asyncio
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

    def test_portable_mode_hides_demo_routes_and_assets(self):
        from fastapi import HTTPException
        from app.main import create_app

        class Registry:
            def catalog_ready(self):
                return True

            def list_certified(self):
                return []

        app = create_app(registered_repository=Registry(), app_mode="portable")
        routes = {
            (route.path, method): route.endpoint
            for route in app.routes
            for method in (getattr(route, "methods", set()) or set())
        }
        self.assertEqual("catalog.html", routes[("/", "GET")]().path.rsplit("/", 1)[-1])
        self.assertEqual({"status": "ok", "database": "connected", "catalog": "ready"}, routes[("/health", "GET")]())
        self.assertEqual({"metrics": []}, routes[("/metrics", "GET")]())
        self.assertNotIn(("/demo/turnover-comparison", "GET"), routes)
        self.assertNotIn(("/ask", "POST"), routes)
        self.assertNotIn(("/certification-request", "POST"), routes)

        static = next(route.app for route in app.routes if getattr(route, "path", None) == "/static")
        with self.assertRaises(HTTPException) as rejected:
            asyncio.run(static.get_response("index.html", {"type": "http"}))
        self.assertEqual(404, rejected.exception.status_code)


if __name__ == "__main__":
    unittest.main()
