import pytest
import respx
from httpx import Response

from services.easyspider_mcp.adapters.http import HTTPAdapter


@pytest.mark.asyncio
@respx.mock
async def test_easyspider_http_contract_uses_real_form_endpoints(tmp_path):
    base = "http://127.0.0.1:8074"
    manage = respx.post(f"{base}/manageTask").mock(return_value=Response(200, text="124"))
    invoke = respx.post(f"{base}/invokeTask").mock(return_value=Response(200, text="310"))
    execute = respx.post(f"{base}/executeTask").mock(return_value=Response(200, json={"success": True}))
    logs = respx.get(f"{base}/getTaskLog", params={"id": 310}).mock(
        return_value=Response(200, json={"data": {"log": "Task execution started"}})
    )
    adapter = HTTPAdapter(base, str(tmp_path))
    assert await adapter.task_register({"id": -1, "graph": []}) == 124
    assert "paras=" in manage.calls[0].request.content.decode()
    assert await adapter.execution_create(124, {"urlList_0": "https://example.com"}) == 310
    assert "id=124" in invoke.calls[0].request.content.decode()
    assert "paras=" in invoke.calls[0].request.content.decode()
    assert (await adapter.execution_start(310))["started"] is True
    assert "id=310" in execute.calls[0].request.content.decode()
    assert (await adapter.execution_logs(310))["status"] == "running"
    assert logs.called
