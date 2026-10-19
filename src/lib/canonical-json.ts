// Recomputes a strategy revision's content hash in the browser.
// The writer (scripts/export_signals_api.py) hashes
//   json.dumps(payload_without_content_hash, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
// and serves the payload unsorted. JSON.parse cannot reproduce Python's number text (`32700.0` would come back
// as `32700`), so this parser keeps every number literal exactly as served and only re-orders keys.

type Node =
  | { kind: "literal"; text: string } // number, true, false, null — verbatim
  | { kind: "string"; value: string }
  | { kind: "array"; items: Node[] }
  | { kind: "object"; entries: [string, Node][] };

const WS = new Set([" ", "\t", "\n", "\r"]);

function parse(text: string): Node {
  let i = 0;
  const skip = () => {
    while (i < text.length && WS.has(text[i])) i++;
  };
  const fail = (what: string): never => {
    throw new Error(`canonical-json: ${what} at ${i}`);
  };
  const readString = (): string => {
    const start = i;
    i++; // opening quote
    while (i < text.length && text[i] !== '"') i += text[i] === "\\" ? 2 : 1;
    if (i >= text.length) fail("unterminated string");
    i++;
    return JSON.parse(text.slice(start, i)) as string;
  };
  const value = (): Node => {
    skip();
    const c = text[i];
    if (c === "{") {
      i++;
      const entries: [string, Node][] = [];
      skip();
      if (text[i] === "}") {
        i++;
        return { kind: "object", entries };
      }
      for (;;) {
        skip();
        if (text[i] !== '"') fail("expected key");
        const key = readString();
        skip();
        if (text[i++] !== ":") fail("expected ':'");
        entries.push([key, value()]);
        skip();
        const sep = text[i++];
        if (sep === "}") return { kind: "object", entries };
        if (sep !== ",") fail("expected ',' or '}'");
      }
    }
    if (c === "[") {
      i++;
      const items: Node[] = [];
      skip();
      if (text[i] === "]") {
        i++;
        return { kind: "array", items };
      }
      for (;;) {
        items.push(value());
        skip();
        const sep = text[i++];
        if (sep === "]") return { kind: "array", items };
        if (sep !== ",") fail("expected ',' or ']'");
      }
    }
    if (c === '"') return { kind: "string", value: readString() };
    const match = /^(?:-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null)/.exec(text.slice(i, i + 64));
    if (!match) fail("unexpected token");
    i += match![0].length;
    return { kind: "literal", text: match![0] };
  };
  const root = value();
  skip();
  if (i !== text.length) fail("trailing data");
  return root;
}

// Python sort_keys orders by code point; compare code points so astral characters sort the same way.
function byCodePoint(a: string, b: string): number {
  const x = Array.from(a);
  const y = Array.from(b);
  for (let k = 0; k < Math.min(x.length, y.length); k++) {
    const d = x[k].codePointAt(0)! - y[k].codePointAt(0)!;
    if (d) return d;
  }
  return x.length - y.length;
}

function serialize(node: Node): string {
  switch (node.kind) {
    case "literal":
      return node.text;
    case "string":
      // JSON.stringify escapes exactly what ensure_ascii=False does: quote, backslash, control characters.
      return JSON.stringify(node.value);
    case "array":
      return `[${node.items.map(serialize).join(",")}]`;
    case "object":
      return `{${[...node.entries]
        .sort(([a], [b]) => byCodePoint(a, b))
        .map(([k, v]) => `${JSON.stringify(k)}:${serialize(v)}`)
        .join(",")}}`;
  }
}

/** Canonical JSON of a served document with one top-level key removed (the embedded hash). */
export function canonicalWithout(text: string, omitKey: string): string {
  const root = parse(text);
  if (root.kind !== "object") throw new Error("canonical-json: top level is not an object");
  return serialize({ kind: "object", entries: root.entries.filter(([k]) => k !== omitKey) });
}

/** sha256 hex of the canonical payload, or null when SubtleCrypto is unavailable (non-secure context). */
export async function revisionContentHash(text: string): Promise<string | null> {
  if (typeof crypto === "undefined" || !crypto.subtle) return null;
  const bytes = new TextEncoder().encode(canonicalWithout(text, "content_hash"));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}
