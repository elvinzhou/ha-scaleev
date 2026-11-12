import json, os, subprocess, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _send(self, data, code=200):
        body = json.dumps({"success": code < 300, "data": data, "message": "x"}).encode()
        self.send_response(code); self.send_header("content-type", "application/json")
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        p = self.path
        if p == "/public/tenants/demo/config": return self._send({"slug": "demo-net", "appName": "Demo Net"})
        if self.headers.get("authorization") != "Bearer tok": return self._send(None, 401)
        if p == "/drivers/locations": return self._send([{"id": "L1", "name": "Lot", "address": "1 St", "stationCount": 2}])
        if p == "/drivers/locations/L1/stations":
            return self._send([
                {"id": "S2", "name": "B2", "type": "AC", "maxPower": 7, "status": "Available", "connectors": [{"connectorId": 1, "type": "J1772", "maxPower": 7}]},
                {"id": "S1", "name": "A1", "type": "AC", "maxPower": 7, "status": "Available", "connectors": [{"connectorId": 1, "type": "J1772", "maxPower": 7}]}])
        self._send(None, 404)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if self.path == "/drivers/auth/login" and body == {"email": "a@b.c", "password": "pw\"x", "tenantSlug": "demo-net"}:
            return self._send({"access_token": "tok", "driver": {"firstName": "Pat"}})
        self._send(None, 401)


class SetupTest(unittest.TestCase):
    def test_flow(self):
        srv = HTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        api = f"http://127.0.0.1:{srv.server_port}"
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "secrets.yaml").write_text('keep_me: "1"\nscaleev_email: "old"\n')
            answers = "\n".join([
                "https://demo.scaleev.scaletech.xyz/map", "a@b.c",
                "2",            # station: second in menu sorted -> B2
                "60",           # kWh
                "binary_sensor.car_cable", "device_tracker.car", "", "notify.mobile_app_pat", ""]) + "\n"
            env = dict(os.environ, SCALEEV_PASSWORD='pw"x')
            r = subprocess.run([sys.executable, str(ROOT / "setup.py"), "--config-dir", d, "--api-base", api],
                               input=answers, capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            auto = (Path(d) / "packages/scaleev_automation.yaml").read_text()
            self.assertIn('station_id: "S2"', auto); self.assertIn("target_kwh: 60", auto)
            self.assertIn("charge_zone: zone.home", auto)
            sec = (Path(d) / "secrets.yaml").read_text()
            self.assertIn('keep_me: "1"', sec); self.assertNotIn('"old"', sec)
            self.assertIn(r'scaleev_password: "pw\"x"', sec)
            self.assertTrue((Path(d) / "secrets.yaml.bak").exists())
            self.assertTrue((Path(d) / "blueprints/automation/scaleev/start_on_plug_in.yaml").exists())
        srv.shutdown()

    def test_bad_password(self):
        srv = HTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        api = f"http://127.0.0.1:{srv.server_port}"
        r = subprocess.run([sys.executable, str(ROOT / "setup.py"), "--config-dir", tempfile.gettempdir(), "--api-base", api],
                           input="demo.scaleev.scaletech.xyz\na@b.c\n", capture_output=True, text=True,
                           env=dict(os.environ, SCALEEV_PASSWORD="wrong"))
        self.assertEqual(r.returncode, 1); self.assertIn("401", r.stderr)
        srv.shutdown()


if __name__ == "__main__":
    unittest.main()
