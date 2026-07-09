"""LangServe-like HTTP compatibility routes for migrated LangChain agents."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse

from agentkit.frameworks._common import (
    UnsupportedFrameworkAgentError,
    chunk_to_text,
    is_input_shape_error,
    maybe_await,
)

logger = logging.getLogger(__name__)

try:
    from langchain_core.messages import HumanMessage
except ImportError:  # pragma: no cover - optional dependency.
    HumanMessage = None  # type: ignore[assignment]


def _normalize_prefix(prefix: str) -> str:
    if prefix == "/":
        return ""
    if prefix and not prefix.startswith("/"):
        raise ValueError("LangServe compatibility prefix must start with '/'.")
    return prefix.rstrip("/")


def _jsonable(value: Any) -> Any:
    try:
        return jsonable_encoder(value)
    except Exception:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))


async def _request_json(request: Request) -> Any:
    try:
        return await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc


def _extract_input(payload: Any) -> Any:
    if isinstance(payload, dict) and "input" in payload:
        return payload["input"]
    return payload


def _request_options(payload: Any) -> tuple[Any, dict[str, Any]]:
    if not isinstance(payload, dict):
        return None, {}
    config = payload.get("config")
    kwargs = payload.get("kwargs")
    return config, kwargs if isinstance(kwargs, dict) else {}


def _input_candidates(value: Any, input_key: str) -> list[Any]:
    candidates: list[Any] = []
    if isinstance(value, dict):
        candidates.append(value)
    elif input_key != "input":
        candidates.append({input_key: value})
        candidates.append(value)
    else:
        candidates.append(value)
        candidates.append({input_key: value})

    if isinstance(value, str) and HumanMessage is not None:
        message = HumanMessage(content=value)
        candidates.extend(([message], {"messages": [message]}))

    deduped: list[Any] = []
    for candidate in candidates:
        if not any(candidate == existing for existing in deduped):
            deduped.append(candidate)
    return deduped


def _method_kwargs(method: Any, config: Any, extra_kwargs: dict[str, Any]) -> dict[str, Any]:
    if config is None and not extra_kwargs:
        return {}

    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return {"config": config, **extra_kwargs} if config is not None else dict(extra_kwargs)

    parameters = signature.parameters
    accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values())
    call_kwargs: dict[str, Any] = {}
    if config is not None and (accepts_kwargs or "config" in parameters):
        call_kwargs["config"] = config
    for key, value in extra_kwargs.items():
        if accepts_kwargs or key in parameters:
            call_kwargs[key] = value
    return call_kwargs


async def _call_with_candidates(
    runnable: Any,
    method_name: str,
    value: Any,
    *,
    input_key: str,
    config: Any = None,
    extra_kwargs: dict[str, Any] | None = None,
) -> Any:
    method = getattr(runnable, method_name, None)
    if not callable(method):
        raise UnsupportedFrameworkAgentError(f"LangChain entry does not expose {method_name}.")

    call_kwargs = _method_kwargs(method, config, extra_kwargs or {})
    last_error: Exception | None = None
    for candidate in _input_candidates(value, input_key):
        try:
            return await maybe_await(method(candidate, **call_kwargs))
        except Exception as exc:
            if not is_input_shape_error(exc):
                raise
            last_error = exc
    if last_error is not None:
        raise last_error
    raise UnsupportedFrameworkAgentError(f"LangChain entry did not accept any {method_name} input shape.")


async def _invoke(
    runnable: Any,
    value: Any,
    *,
    input_key: str,
    config: Any = None,
    extra_kwargs: dict[str, Any] | None = None,
) -> Any:
    if callable(getattr(runnable, "ainvoke", None)):
        return await _call_with_candidates(
            runnable,
            "ainvoke",
            value,
            input_key=input_key,
            config=config,
            extra_kwargs=extra_kwargs,
        )
    if callable(getattr(runnable, "invoke", None)):
        return await _call_with_candidates(
            runnable,
            "invoke",
            value,
            input_key=input_key,
            config=config,
            extra_kwargs=extra_kwargs,
        )
    if callable(runnable):
        call_kwargs = _method_kwargs(runnable, config, extra_kwargs or {})
        last_error: Exception | None = None
        for candidate in _input_candidates(value, input_key):
            try:
                return await maybe_await(runnable(candidate, **call_kwargs))
            except Exception as exc:
                if not is_input_shape_error(exc):
                    raise
                last_error = exc
        if last_error is not None:
            raise last_error
    raise UnsupportedFrameworkAgentError(
        "LangChain entry must expose ainvoke/invoke or be callable for LangServe compatibility."
    )


async def _stream_from_method(
    method: Any,
    value: Any,
    *,
    input_key: str,
    config: Any = None,
    extra_kwargs: dict[str, Any] | None = None,
) -> AsyncGenerator[Any, None]:
    call_kwargs = _method_kwargs(method, config, extra_kwargs or {})
    last_error: Exception | None = None
    for candidate in _input_candidates(value, input_key):
        emitted = False
        try:
            stream = method(candidate, **call_kwargs)
            if inspect.isawaitable(stream):
                stream = await stream
            if hasattr(stream, "__aiter__"):
                async for chunk in stream:
                    emitted = True
                    yield chunk
            else:
                for chunk in stream:
                    emitted = True
                    yield chunk
            return
        except Exception as exc:
            if emitted:
                raise
            if not is_input_shape_error(exc):
                raise
            last_error = exc
    if last_error is not None:
        raise last_error


def _event_stream_kwargs(method: Any) -> dict[str, str]:
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return {}
    version = signature.parameters.get("version")
    if version is not None and version.default is inspect.Parameter.empty:
        return {"version": "v2"}
    return {}


async def _stream_from_method_with_candidates(
    method: Any,
    value: Any,
    *,
    input_key: str,
    kwargs: dict[str, Any] | None = None,
    config: Any = None,
    extra_kwargs: dict[str, Any] | None = None,
) -> AsyncGenerator[Any, None]:
    call_kwargs = {**_method_kwargs(method, config, extra_kwargs or {}), **(kwargs or {})}
    last_error: Exception | None = None
    for candidate in _input_candidates(value, input_key):
        emitted = False
        try:
            stream = method(candidate, **call_kwargs)
            if inspect.isawaitable(stream):
                stream = await stream
            if hasattr(stream, "__aiter__"):
                async for chunk in stream:
                    emitted = True
                    yield chunk
            else:
                for chunk in stream:
                    emitted = True
                    yield chunk
            return
        except Exception as exc:
            if emitted:
                raise
            if not is_input_shape_error(exc):
                raise
            last_error = exc
    if last_error is not None:
        raise last_error


async def _stream_chunks(
    runnable: Any,
    value: Any,
    *,
    input_key: str,
    config: Any = None,
    extra_kwargs: dict[str, Any] | None = None,
) -> AsyncGenerator[Any, None]:
    astream = getattr(runnable, "astream", None)
    if callable(astream):
        async for chunk in _stream_from_method(
            astream,
            value,
            input_key=input_key,
            config=config,
            extra_kwargs=extra_kwargs,
        ):
            yield chunk
        return

    stream = getattr(runnable, "stream", None)
    if callable(stream):
        async for chunk in _stream_from_method(
            stream,
            value,
            input_key=input_key,
            config=config,
            extra_kwargs=extra_kwargs,
        ):
            yield chunk
        return

    yield await _invoke(runnable, value, input_key=input_key, config=config, extra_kwargs=extra_kwargs)


async def _batch(
    runnable: Any,
    values: list[Any],
    *,
    input_key: str,
    config: Any = None,
    extra_kwargs: dict[str, Any] | None = None,
) -> list[Any]:
    abatch = getattr(runnable, "abatch", None)
    if callable(abatch):
        try:
            return await maybe_await(abatch(values, **_method_kwargs(abatch, config, extra_kwargs or {})))
        except Exception:
            logger.debug("LangServe compat abatch failed; falling back to per-item invoke.", exc_info=True)

    batch = getattr(runnable, "batch", None)
    if callable(batch):
        try:
            return await maybe_await(batch(values, **_method_kwargs(batch, config, extra_kwargs or {})))
        except Exception:
            logger.debug("LangServe compat batch failed; falling back to per-item invoke.", exc_info=True)

    return await asyncio.gather(
        *[_invoke(runnable, value, input_key=input_key, config=config, extra_kwargs=extra_kwargs) for value in values]
    )


def _sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(_jsonable(data), ensure_ascii=False)}\n\n"


def _route_path(prefix: str, path: str) -> str:
    return f"{prefix}{path}" if prefix else path


def _promote_route(app: FastAPI, endpoint: Any, path: str) -> None:
    routes = app.router.routes
    route_index = next(
        (
            index
            for index, route in enumerate(routes)
            if getattr(route, "endpoint", None) is endpoint and getattr(route, "path", None) == path
        ),
        None,
    )
    if route_index is None:
        return

    route = routes.pop(route_index)
    insert_at = len(routes)
    for index, existing in enumerate(routes):
        existing_path = getattr(existing, "path", None)
        if existing_path == path or existing_path in {"", "/"}:
            insert_at = index
            break
    routes.insert(insert_at, route)


def attach_langserve_compat_routes(
    app: FastAPI,
    runnable: Any,
    *,
    input_key: str = "input",
    prefix: str = "",
) -> None:
    """Attach a conservative LangServe-like HTTP contract to a FastAPI app."""

    normalized_prefix = _normalize_prefix(prefix)
    logger.info("Attaching LangServe compatibility routes at prefix %s", normalized_prefix or "/")

    async def invoke(request: Request) -> JSONResponse:
        payload = await _request_json(request)
        config, extra_kwargs = _request_options(payload)
        run_id = str(uuid4())
        try:
            output = await _invoke(
                runnable,
                _extract_input(payload),
                input_key=input_key,
                config=config,
                extra_kwargs=extra_kwargs,
            )
        except UnsupportedFrameworkAgentError as exc:
            logger.info("LangServe compat invoke rejected unsupported runnable: %s", exc)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception:
            logger.exception("LangServe compat invoke failed.")
            raise
        return JSONResponse({"output": _jsonable(output), "metadata": {"run_id": run_id}})

    async def batch(request: Request) -> JSONResponse:
        payload = await _request_json(request)
        config, extra_kwargs = _request_options(payload)
        raw_inputs = payload.get("inputs") if isinstance(payload, dict) else None
        if not isinstance(raw_inputs, list):
            raise HTTPException(status_code=400, detail="Batch request body must include an 'inputs' list.")
        outputs = await _batch(runnable, raw_inputs, input_key=input_key, config=config, extra_kwargs=extra_kwargs)
        return JSONResponse(
            {
                "output": _jsonable(outputs),
                "metadata": {"run_ids": [str(uuid4()) for _ in outputs]},
            }
        )

    async def stream(request: Request) -> StreamingResponse:
        payload = await _request_json(request)
        value = _extract_input(payload)
        config, extra_kwargs = _request_options(payload)

        async def events() -> AsyncGenerator[str, None]:
            try:
                async for chunk in _stream_chunks(
                    runnable,
                    value,
                    input_key=input_key,
                    config=config,
                    extra_kwargs=extra_kwargs,
                ):
                    yield _sse("data", chunk)
                yield _sse("end", {})
            except Exception as exc:
                logger.exception("LangServe compat stream failed.")
                yield _sse("error", {"message": str(exc)})

        return StreamingResponse(events(), media_type="text/event-stream")

    async def stream_events(request: Request) -> StreamingResponse:
        payload = await _request_json(request)
        value = _extract_input(payload)
        config, extra_kwargs = _request_options(payload)
        astream_events = getattr(runnable, "astream_events", None)

        async def events() -> AsyncGenerator[str, None]:
            try:
                if callable(astream_events):
                    async for event in _stream_from_method_with_candidates(
                        astream_events,
                        value,
                        input_key=input_key,
                        kwargs=_event_stream_kwargs(astream_events),
                        config=config,
                        extra_kwargs=extra_kwargs,
                    ):
                        yield _sse("data", event)
                else:
                    async for chunk in _stream_chunks(
                        runnable,
                        value,
                        input_key=input_key,
                        config=config,
                        extra_kwargs=extra_kwargs,
                    ):
                        yield _sse(
                            "data",
                            {
                                "event": "on_chain_stream",
                                "data": {"chunk": _jsonable(chunk), "text": chunk_to_text(chunk)},
                            },
                        )
                yield _sse("end", {})
            except Exception as exc:
                logger.exception("LangServe compat stream_events failed.")
                yield _sse("error", {"message": str(exc)})

        return StreamingResponse(events(), media_type="text/event-stream")

    async def stream_log(request: Request) -> StreamingResponse:
        payload = await _request_json(request)
        value = _extract_input(payload)
        config, extra_kwargs = _request_options(payload)
        astream_log = getattr(runnable, "astream_log", None)
        if not callable(astream_log):
            logger.info("LangServe compat /stream_log requested but runnable does not expose astream_log.")
            raise HTTPException(
                status_code=501,
                detail="stream_log requires a runnable exposing astream_log; this compatibility layer does not synthesize LangServe logs.",
            )

        async def events() -> AsyncGenerator[str, None]:
            try:
                async for event in _stream_from_method_with_candidates(
                    astream_log,
                    value,
                    input_key=input_key,
                    config=config,
                    extra_kwargs=extra_kwargs,
                ):
                    yield _sse("data", event)
                yield _sse("end", {})
            except Exception as exc:
                logger.exception("LangServe compat stream_log failed.")
                yield _sse("error", {"message": str(exc)})

        return StreamingResponse(events(), media_type="text/event-stream")

    routes: list[tuple[str, Any]] = [
        ("/invoke", invoke),
        ("/batch", batch),
        ("/stream", stream),
        ("/stream_events", stream_events),
        ("/stream_log", stream_log),
    ]
    for path, endpoint in routes:
        full_path = _route_path(normalized_prefix, path)
        app.add_api_route(full_path, endpoint, methods=["POST"])
        _promote_route(app, endpoint, full_path)
