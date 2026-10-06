/** Small, safe markdown renderer for what agents write (summaries, notes, chat replies): headings, bullet and numbered
 *  lists, quotes, fenced code, **bold**, *italic*, `code`, and links. Everything goes through React, so nothing is
 *  ever interpreted as HTML; links are shown as links only when they are http(s). */
import type { ReactNode } from "react";

const SAFE_LINK = /^https?:\/\/[^\s]+$/i;
// code, [[note]], [label](url), **bold**, *italic*, _italic_ (not inside_words)
const INLINE = /(`[^`\n]+`|\[\[[^\]]+\]\]|\[[^\]\n]+\]\([^)\s]+\)|\*\*[^*\n]+\*\*|\*[^*\s][^*\n]*\*|(?<![\w])_[^_\n]+_(?![\w]))/g;

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let k = 0;
  for (const m of text.matchAll(INLINE)) {
    const idx = m.index ?? 0;
    if (idx > last) out.push(text.slice(last, idx));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<b key={k++}>{inline(tok.slice(2, -2))}</b>);
    else if (tok.startsWith("`")) out.push(<code key={k++}>{tok.slice(1, -1)}</code>);
    else if (tok.startsWith("[[")) out.push(<code key={k++}>{tok.slice(2, -2)}</code>);
    else if (tok.startsWith("[")) {
      const close = tok.indexOf("](");
      const label = tok.slice(1, close);
      const url = tok.slice(close + 2, -1);
      if (SAFE_LINK.test(url)) out.push(<a key={k++} href={url} target="_blank" rel="noreferrer noopener">{label}</a>);
      else out.push(tok);  // javascript:, data:, relative… stay plain text
    } else out.push(<i key={k++}>{tok.slice(1, -1)}</i>);
    last = idx + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text, variant }: { text: string; variant?: "chat" }) {
  const blocks: ReactNode[] = [];
  let ul: string[] = [];
  let ol: string[] = [];
  let quote: string[] = [];
  const flush = () => {
    if (ul.length) blocks.push(<ul key={blocks.length}>{ul.map((l, i) => <li key={i}>{inline(l)}</li>)}</ul>);
    if (ol.length) blocks.push(<ol key={blocks.length}>{ol.map((l, i) => <li key={i}>{inline(l)}</li>)}</ol>);
    if (quote.length) blocks.push(<blockquote key={blocks.length}>{quote.map((l, i) => <p key={i}>{inline(l)}</p>)}</blockquote>);
    ul = []; ol = []; quote = [];
  };
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trimEnd();
    if (line.startsWith("```")) {  // fenced code: up to the closing fence (or the end)
      flush();
      const code: string[] = [];
      for (i++; i < lines.length && !lines[i].startsWith("```"); i++) code.push(lines[i]);
      blocks.push(<pre key={blocks.length}><code>{code.join("\n")}</code></pre>);
      continue;
    }
    const bullet = /^\s*[-*•] +(.*)$/.exec(line);
    const numbered = /^\s*\d+[.)] +(.*)$/.exec(line);
    const quoted = /^>\s?(.*)$/.exec(line);
    if (bullet) { if (ol.length || quote.length) flush(); ul.push(bullet[1]); continue; }
    if (numbered) { if (ul.length || quote.length) flush(); ol.push(numbered[1]); continue; }
    if (quoted) { if (ul.length || ol.length) flush(); quote.push(quoted[1]); continue; }
    flush();
    if (!line.trim()) continue;
    const h = /^(#{1,3}) +(.*)$/.exec(line);
    if (h) {
      const Tag = (`h${h[1].length}`) as "h1" | "h2" | "h3";
      blocks.push(<Tag key={blocks.length}>{inline(h[2])}</Tag>);
    } else blocks.push(<p key={blocks.length}>{inline(line)}</p>);
  }
  flush();
  return <div className={variant === "chat" ? "md chat" : "md"}>{blocks}</div>;
}
