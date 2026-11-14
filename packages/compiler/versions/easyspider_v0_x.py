from __future__ import annotations

from typing import Any

from packages.crawl_core.errors import CompileError
from packages.crawl_core.models import Operation, PlanStep
from packages.compiler.selector_lowering import lower_fallbacks, lower_selector

from .base import EasySpiderVersionProfile


def _common(*, xpath: str = "", wait: float = 0, use_loop: bool = False) -> dict[str, Any]:
    return {
        "history": 1,
        "tabIndex": 0,
        "useLoop": use_loop,
        "xpath": xpath,
        "iframe": False,
        "wait": wait,
        "waitType": 0,
        "beforeJS": "",
        "beforeJSWaitTime": 0,
        "afterJS": "",
        "afterJSWaitTime": 0,
    }


class EasySpiderV035Profile(EasySpiderVersionProfile):
    version = "0.3.5"

    option = {
        Operation.OPEN_URL: 1,
        Operation.CLICK: 2,
        Operation.EXTRACT: 3,
        Operation.INPUT: 4,
        Operation.CUSTOM: 5,
        Operation.MOVE: 7,
        Operation.LOOP_ELEMENTS: 8,
        Operation.LOOP_PAGES: 8,
        Operation.CONDITION: 10,
        Operation.WAIT: 5,
        Operation.BACK: 5,
    }

    titles = {
        Operation.OPEN_URL: "打开网页",
        Operation.CLICK: "点击元素",
        Operation.EXTRACT: "提取数据",
        Operation.INPUT: "输入文字",
        Operation.CUSTOM: "自定义操作",
        Operation.MOVE: "移动到元素",
        Operation.LOOP_ELEMENTS: "循环元素",
        Operation.LOOP_PAGES: "循环翻页",
        Operation.CONDITION: "条件",
        Operation.WAIT: "等待页面",
        Operation.BACK: "返回列表页",
    }

    def lower(self, step: PlanStep, *, in_loop: bool, start_url: str) -> tuple[int, str, dict[str, Any]]:
        try:
            option = self.option[step.op]
        except KeyError as exc:
            raise CompileError(f"unsupported operation for {self.version}: {step.op}") from exc
        title = self.titles[step.op]

        if step.op == Operation.OPEN_URL:
            url = start_url if step.url == "{{start_url}}" else str(step.url)
            params = _common()
            params.update(
                url=url,
                links=url,
                maxWaitTime=10,
                scrollType=0,
                scrollCount=1,
                scrollWaitTime=1,
                cookies="",
            )
            return option, title, params

        if step.op == Operation.EXTRACT:
            xpath = lower_selector(str(step.effective_selector), step.selector_type)
            all_xpaths = lower_fallbacks(step.fallbacks, step.selector_type)
            node_type = 0
            content_type = 0
            js_value = ""
            if step.source == "html":
                content_type = 2  # innerHTML
            elif step.source in {"attribute", "url"}:
                attribute = step.attribute or "href"
                if attribute == "href":
                    node_type = 2  # EasySpider's link node extracts href
                elif attribute == "src":
                    node_type = 4  # EasySpider's image node extracts src
                else:
                    content_type = 14  # arbitrary element attribute
                    js_value = attribute
            param = {
                "nodeType": node_type,
                "contentType": content_type,
                "relative": bool(step.relative_selector),
                "name": step.field,
                "desc": "",
                "extractType": 0,
                "relativeXPath": xpath,
                "allXPaths": all_xpaths,
                "exampleValues": [],
                "default": "",
                "beforeJS": "",
                "beforeJSWaitTime": 0,
                "JS": js_value,
                "JSWaitTime": 0,
                "afterJS": "",
                "afterJSWaitTime": 0,
                "downloadPic": 0,
            }
            params = _common(use_loop=bool(step.relative_selector and in_loop))
            # EasySpider 0.3.5's execution stage reads extraction definitions
            # from ``paras``. Later releases migrate this key to ``params``.
            params["paras"] = [param]
            return option, title, params

        if step.op in {Operation.CLICK, Operation.INPUT, Operation.MOVE}:
            xpath = lower_selector(str(step.effective_selector), step.selector_type)
            params = _common(xpath=xpath, use_loop=bool(step.relative_selector and in_loop))
            params.update(allXPaths=lower_fallbacks(step.fallbacks, step.selector_type))
            if step.op == Operation.CLICK:
                params.update(
                    history=4 if in_loop else 1,
                    tabIndex=-1,
                    clickWay=0,
                    maxWaitTime=10,
                    params=[],
                    scrollType=0,
                    scrollCount=1,
                    scrollWaitTime=1,
                    loopType=1 if in_loop else 0,
                )
            elif step.op == Operation.INPUT:
                params["value"] = step.value or ""
            return option, title, params

        if step.op in {Operation.LOOP_ELEMENTS, Operation.LOOP_PAGES}:
            xpath = "//body" if step.op == Operation.LOOP_PAGES else lower_selector(str(step.selector), step.selector_type)
            params = _common(xpath=xpath)
            params.update(
                history=4 if step.op == Operation.LOOP_ELEMENTS else 1,
                tabIndex=-1,
                scrollType=0,
                scrollCount=1,
                scrollWaitTime=1,
                loopType=1 if step.op == Operation.LOOP_ELEMENTS else 0,
                pathList="",
                textList="",
                code="",
                waitTime=0,
                exitCount=0 if step.op == Operation.LOOP_ELEMENTS else int(step.max_iterations or 1),
                historyWait=2,
                breakMode=0,
                breakCode="",
                breakCodeWaitTime=0,
                allXPaths=lower_fallbacks(step.fallbacks, step.selector_type),
            )
            return option, title, params

        if step.op in {Operation.CUSTOM, Operation.WAIT, Operation.BACK}:
            params = _common(wait=step.seconds or 0)
            code = step.code or "return true;"
            if step.op == Operation.BACK:
                # Normally a containing EasySpider loop restores browser history.
                # This explicit operation remains useful outside such a loop.
                code = "window.history.back(); return true;"
            params.update(codeMode=0, code=code, waitTime=step.seconds or 0, recordASField=0)
            return option, title, params

        raise CompileError(f"{step.op} is declared but not implemented in profile {self.version}")
