export async function onRequestPost(context) {
  const apiKey = context.env.GEMINI_API_KEY;
  if (!apiKey) {
    return jsonResponse(
      { error: "GEMINI_API_KEY is not configured in Cloudflare environment variables." },
      500,
    );
  }

  let body;
  try {
    body = await context.request.json();
  } catch {
    return jsonResponse({ error: "Request body must be JSON." }, 400);
  }

  const model = body.model || "gemini-2.5-flash";
  const payload = {
    contents: body.contents,
    generationConfig: body.generationConfig,
  };

  if (!Array.isArray(payload.contents)) {
    return jsonResponse({ error: "Missing contents array." }, 400);
  }

  const response = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(
      model,
    )}:generateContent`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-goog-api-key": apiKey,
      },
      body: JSON.stringify(payload),
    },
  );

  const text = await response.text();
  return new Response(text, {
    status: response.status,
    headers: {
      "Content-Type": response.headers.get("Content-Type") || "application/json",
      "Cache-Control": "no-store",
    },
  });
}

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
}
