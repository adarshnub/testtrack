from fastapi import FastAPI, Request, Response

from demo import MAX_UPLOAD, json_response, route

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.api_route("/{path:path}", methods=["GET", "POST"])
async def demo(path: str, request: Request):
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_UPLOAD:
            status, headers, content = json_response(413, {"error": "Choose a PDF smaller than 4 MB."})
            return Response(content, status_code=status, headers=headers)
        body.extend(chunk)
    status, headers, content = route(request.method, "/" + path, dict(request.headers), bytes(body))
    return Response(content, status_code=status, headers=headers)
