"""Offline browser smoke check using the real provider routes and fake inference."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def main():
    with tempfile.TemporaryDirectory(prefix='.pytest-provider-ui-', dir=ROOT) as temporary:
        config_path = Path(temporary) / 'provider.json'
        os.environ['PROVIDER_CONFIG_PATH'] = str(config_path)
        os.environ['LLM_API_KEY'] = 'test-browser-key'
        from fastapi.testclient import TestClient
        from research_agent.api import app
        from research_agent import api as task_api
        from research_agent.persistence.repository import Repository
        from research_agent.core.config import get_settings
        from research_agent.api_routes import providers as provider_api
        get_settings.cache_clear()
        repo = Repository('sqlite:///' + str(Path(temporary) / 'tasks.db'))
        task_api.get_repository = lambda: repo
        probes = []
        class FakeClient:
            def __init__(self, **kwargs):
                probes.append(kwargs)
                self.chat = self.completions = self
            def create(self, **kwargs):
                return type('Reply', (), {'choices': [type('Choice', (), {'message': type('Message', (), {'content': 'OK'})()})()]})()
            def close(self):
                pass
        provider_api.OpenAI = FakeClient
        client = TestClient(app)
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=ROOT / 'frontend/dist'))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, channel='msedge')
                page = browser.new_page(viewport={'width': 1280, 'height': 900})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                def intercept(route):
                    url = route.request.url
                    path = url.removeprefix(origin)
                    if not url.startswith(origin):
                        route.abort()
                    elif path.startswith(('/settings/provider', '/tasks', '/research')):
                        response = client.request(route.request.method, path,
                                                  content=route.request.post_data,
                                                  headers={'Content-Type': 'application/json'})
                        route.fulfill(status=response.status_code, content_type='application/json', body=response.text)
                    elif path.startswith('/health'):
                        route.fulfill(json={'status': 'ok'})
                    elif path.startswith('/workspaces'):
                        route.fulfill(json={'workspaces': [], 'allowed_roots': []})
                    else:
                        route.continue_()
                page.route('**/*', intercept)
                page.goto(origin)
                page.get_by_role('button', name='头像设置', exact=True).click()
                page.get_by_role('button', name='模型 Provider', exact=True).click()
                page.get_by_label('服务名称').fill('Browser A')
                page.get_by_label('API 地址').fill('https://a.example/v1')
                page.get_by_label('API Key').fill('browser-key-a')
                page.get_by_label('模型列表').fill('a-one\na-two')
                page.get_by_role('button', name='测试连接', exact=True).click()
                page.get_by_text('连接成功，模型已返回有效响应。').wait_for()
                assert not config_path.exists(), 'Test must not save configuration'
                page.get_by_role('button', name='保存服务', exact=True).click()
                page.get_by_text('服务已保存，可以在对话输入框旁选择模型。').wait_for()
                assert page.get_by_label('API Key').input_value() == ''
                page.get_by_role('button', name='新增服务', exact=True).click()
                page.get_by_label('服务名称').fill('Browser B')
                page.get_by_label('API 地址').fill('https://b.example/v1')
                page.get_by_label('API Key').fill('browser-key-b')
                page.get_by_label('模型列表').fill('b-one\nb-two')
                page.get_by_label('默认模型').select_option('b-two')
                page.get_by_role('button', name='测试连接', exact=True).click()
                page.get_by_text('连接成功，模型已返回有效响应。').wait_for()
                assert len(json.loads(config_path.read_text(encoding='utf-8'))['providers']) == 1
                assert probes[-1]['api_key'] == 'browser-key-b'
                page.get_by_role('button', name='保存服务', exact=True).click()
                page.get_by_text('服务已保存，可以在对话输入框旁选择模型。').wait_for()
                page.locator('.provider-roles summary').click()
                page.get_by_label('规划模型').select_option('b-one')
                page.get_by_role('button', name='保存服务', exact=True).click()
                page.get_by_text('服务已保存，可以在对话输入框旁选择模型。').wait_for()
                page.get_by_role('button', name='将已保存的服务设为默认', exact=True).click()
                page.get_by_text('已设为默认服务。', exact=True).wait_for()
                page.locator('.agent-utility-panel').evaluate('(element) => { element.scrollTop = 0 }')
                page.screenshot(path=str(ROOT / '.pytest-provider-desktop.png'), full_page=True, animations='disabled')
                page.get_by_role('button', name='关闭面板', exact=True).click()
                page.get_by_label('使用模型').select_option(label='Browser B / b-two')
                page.get_by_label('发送消息给砚台').fill('Browser model selection test')
                page.get_by_role('button', name='发送消息', exact=True).click()
                page.get_by_role('main').get_by_text('Browser model selection test', exact=True).wait_for()
                record = repo.list_runs()[0]
                assert record['payload']['model_selection']['model'] == 'b-two'
                assert 'browser-key-b' not in json.dumps(record)
                page.get_by_role('button', name='新对话', exact=True).click()
                page.get_by_label('使用模型').select_option(label='Browser A / a-two')
                page.get_by_role('button', name='Agent', exact=True).click()
                page.locator('.composer-options input[type=checkbox]').first.check()
                page.get_by_label('发送消息给砚台').fill('Browser research model test')
                page.get_by_role('button', name='发送消息', exact=True).click()
                page.get_by_role('main').get_by_text('Browser research model test', exact=True).wait_for()
                record = repo.list_runs()[0]
                assert record['payload']['model_selection']['model'] == 'a-two'
                page.get_by_role('button', name='头像设置', exact=True).click()
                page.get_by_role('button', name='模型 Provider', exact=True).click()
                assert page.get_by_label('默认模型').input_value() == 'b-two'
                page.on('dialog', lambda dialog: dialog.accept())
                page.get_by_role('button', name='删除服务', exact=True).click()
                page.get_by_text('服务已删除，已有任务继续使用提交时的配置。').wait_for()
                assert len(client.get('/settings/providers').json()['providers']) == 1
                page.set_viewport_size({'width': 390, 'height': 844})
                page.get_by_role('button', name='关闭面板', exact=True).click()
                page.get_by_role('button', name='打开会话列表', exact=True).click()
                page.get_by_role('button', name='头像设置', exact=True).click()
                page.get_by_role('button', name='模型 Provider', exact=True).click()
                page.get_by_label('默认模型').wait_for()
                page.screenshot(path=str(ROOT / '.pytest-provider-mobile.png'), full_page=True, animations='disabled')
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert not errors, errors
                browser.close()
                print('Provider browser smoke passed: multiple services/default/delete/independent keys/agent and research model submissions/mobile layout.')
        finally:
            server.shutdown()
            server.server_close()
            client.close()
            repo.engine.dispose()


if __name__ == '__main__':
    main()
