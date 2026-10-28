"""API wiring smoke checks; skipped when FastAPI is not installed."""

import importlib.util
import asyncio
import json
import unittest


def _request(app, method, path, payload=None, headers=()):
    """Exercise the real ASGI routing and dependency chain without httpx."""

    async def run():
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        request_headers = [(b"host", b"testserver")]
        if payload is not None:
            request_headers.append((b"content-type", b"application/json"))
        request_headers.extend((name.lower().encode("ascii"), value.encode("ascii")) for name, value in headers)
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": request_headers,
            "client": ("testclient", 12345),
            "server": ("testserver", 80),
        }
        sent = []
        delivered = False

        async def receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        await app(scope, receive, send)
        status = next(item["status"] for item in sent if item["type"] == "http.response.start")
        content = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
        return status, json.loads(content) if content else None

    return asyncio.run(run())


class _PortableRegistry:
    def __init__(self):
        self.query_calls = []
        self.scoped_only = None

    def catalog_ready(self):
        return True

    def list_certified(self, *, scoped_only=False):
        self.scoped_only = scoped_only
        return []

    def query(self, metric_id, start_date, end_date, dimensions, filters, *, user_id):
        self.query_calls.append((metric_id, start_date, end_date, dimensions, filters, user_id))
        return {"tier": "CERTIFIED", "rows": [{"value": 1}], "access": {"user_id": user_id}}


class _PortableSecurity:
    def __init__(self):
        self.grants = {
            "reader-1": [{"location_key": "101", "display_name": "Site 101"}],
            "admin-1": [{"location_key": "101", "display_name": "Site 101"}],
            "empty-1": [],
        }

    def security_ready(self):
        return True

    def authenticate_token(self, token):
        from app.security import Principal

        identities = {
            "reader-token": Principal("reader-1", "Reader", "READER"),
            "admin-token": Principal("admin-1", "Admin", "ADMIN"),
            "empty-token": Principal("empty-1", "No grants", "READER"),
        }
        return identities.get(token)

    def active_locations(self, user_id):
        return self.grants[user_id]


class _Explorer:
    def __init__(self):
        self.calls = []

    def generate_sql(self, question):
        self.calls.append(question)
        return {
            "tier": "EXPLORATORY", "generated_sql": "SELECT COUNT(*) FROM WH_ALLOWED_VIEW",
            "executed": False, "certified": False,
        }


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

        registry = _PortableRegistry()
        app = create_app(
            registered_repository=registry,
            security_repository=_PortableSecurity(),
            app_mode="portable",
            select_ai_enabled=False,
        )
        routes = {
            (route.path, method): route.endpoint
            for route in app.routes
            for method in (getattr(route, "methods", set()) or set())
        }
        self.assertEqual("catalog.html", routes[("/", "GET")]().path.rsplit("/", 1)[-1])
        self.assertEqual({
            "status": "ok", "database": "connected", "catalog": "ready",
            "security": "ready", "auth": "bearer",
        }, routes[("/health", "GET")]())
        self.assertEqual((401, {"detail": "Bearer token required."}), _request(app, "GET", "/metrics"))
        self.assertEqual((200, {"metrics": []}), _request(
            app, "GET", "/metrics", headers=(("authorization", "Bearer reader-token"),),
        ))
        self.assertTrue(registry.scoped_only)
        self.assertNotIn(("/demo/turnover-comparison", "GET"), routes)
        self.assertNotIn(("/ask", "POST"), routes)
        self.assertNotIn(("/certification-request", "POST"), routes)
        self.assertNotIn(("/select-ai/preview", "POST"), routes)

        static = next(route.app for route in app.routes if getattr(route, "path", None) == "/static")
        with self.assertRaises(HTTPException) as rejected:
            asyncio.run(static.get_response("index.html", {"type": "http"}))
        self.assertEqual(404, rejected.exception.status_code)

    def test_portable_auth_and_location_grants_bound_to_verified_identity(self):
        from app.main import create_app

        registry = _PortableRegistry()
        app = create_app(
            registered_repository=registry,
            security_repository=_PortableSecurity(),
            app_mode="portable", select_ai_enabled=False,
        )
        query = {
            "metric_id": "FILLED_REQUISITIONS", "start_date": "2026-07-01",
            "end_date": "2026-10-01", "filters": {"STATE": "FL"},
            "user_id": "admin-1",  # Extra client data must not determine scope.
        }
        for auth in ((), (("authorization", "Bearer revoked-token"),),
                     (("authorization", "Basic reader-token"),),
                     (("authorization", "Bearer reader-token"), ("authorization", "Bearer admin-token"))):
            with self.subTest(auth=auth):
                status, _ = _request(app, "POST", "/metric-query", query, auth)
                self.assertEqual(401, status)
        self.assertEqual([], registry.query_calls)

        status, _ = _request(app, "POST", "/metric-query", query,
                             (("authorization", "Bearer empty-token"),))
        self.assertEqual(403, status)
        self.assertEqual([], registry.query_calls)

        status, answer = _request(app, "POST", "/metric-query", query,
                                  (("authorization", "Bearer reader-token"),))
        self.assertEqual(200, status)
        self.assertEqual(answer["access"]["user_id"], "reader-1")
        self.assertEqual(registry.query_calls[0][-1], "reader-1")
        self.assertEqual(registry.query_calls[0][4], {"STATE": "FL"})
        self.assertEqual((200, {
            "user_id": "reader-1", "display_name": "Reader", "role": "READER",
            "locations": [{"location_key": "101", "display_name": "Site 101"}],
        }), _request(app, "GET", "/me", headers=(("authorization", "Bearer reader-token"),)))

    def test_select_ai_preview_is_admin_only_and_never_claims_certification(self):
        from app.main import create_app

        explorer = _Explorer()
        registry = _PortableRegistry()
        app = create_app(
            registered_repository=registry,
            security_repository=_PortableSecurity(),
            select_ai_explorer=explorer,
            app_mode="portable", select_ai_enabled=True,
        )
        payload = {"question": "How many filled requisitions?"}
        status, _ = _request(app, "POST", "/select-ai/preview", payload,
                             (("authorization", "Bearer reader-token"),))
        self.assertEqual(403, status)
        self.assertEqual(explorer.calls, [])

        status, body = _request(app, "POST", "/select-ai/preview", payload,
                                (("authorization", "Bearer admin-token"),))
        self.assertEqual(200, status)
        self.assertEqual(explorer.calls, [payload["question"]])
        self.assertEqual(body["tier"], "EXPLORATORY")
        self.assertFalse(body["executed"])
        self.assertFalse(body["certified"])
        self.assertEqual(registry.query_calls, [])


if __name__ == "__main__":
    unittest.main()
