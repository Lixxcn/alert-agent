from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
import time


class SimpleAlertHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers["Content-Length"])
        post_data = self.rfile.read(content_length)
        try:
            alert_data = json.loads(post_data.decode("utf-8"))
            print(f"Received alert: {json.dumps(alert_data, indent=2)}")
            # 将接收到的告警数据传递给一个回调函数，该函数将触发 PocketFlow 流程
            if hasattr(self.server, "alert_callback"):
                self.server.alert_callback(alert_data)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Alert received successfully")
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Invalid JSON")
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Server error: {e}".encode("utf-8"))


class AlertWebhookServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, alert_callback):
        super().__init__(server_address, RequestHandlerClass)
        self.alert_callback = alert_callback


def start_webhook_server(host, port, alert_callback):
    server_address = (host, port)
    httpd = AlertWebhookServer(server_address, SimpleAlertHandler, alert_callback)
    print(f"Starting Alert Webhook Server on http://{host}:{port}/alert")
    # 在一个单独的线程中运行服务器，以便主程序可以继续执行
    server_thread = threading.Thread(target=httpd.serve_forever)
    server_thread.daemon = True  # 设置为守护线程，主程序退出时自动关闭
    server_thread.start()
    return httpd, server_thread


if __name__ == "__main__":
    # 这是一个简单的测试回调函数
    def test_callback(alert_data):
        print("Test callback received alert data:")
        print(json.dumps(alert_data, indent=2))

    # 启动服务器，监听 10000 端口
    server, thread = start_webhook_server("0.0.0.0", 10000, test_callback)
    try:
        # 保持主线程运行，以便服务器线程可以继续
        print("Server running. Press Ctrl+C to stop.")

        # 服务器线程将由 httpd.serve_forever() 阻塞，无需额外的 sleep
        # 保持主线程运行，以便服务器线程可以继续
        # 这里使用一个简单的循环来保持主线程活跃，直到 KeyboardInterrupt
        while True:
            time.sleep(1)  # 使用 time.sleep 避免 CPU 占用过高
    except KeyboardInterrupt:
        print("Stopping server...")
        server.shutdown()
        server.server_close()
        thread.join()
        print("Server stopped.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        server.shutdown()
        server.server_close()
        thread.join()
