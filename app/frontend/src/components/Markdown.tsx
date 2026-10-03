/** Minimal, safe markdown renderer for agent-written summaries (headings, lists, bold, code, links). */
import type { ReactNode } from "react";

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\[\[[^\]]+\]\]|_[^_]+_)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**")) out.push(<b key={k++}>{t.slice(2, -2)}</b>);
    else if (t.startsWith("`")) out.push(<code key={k++}>{t.slice(1, -1)}</code>);
    else if (t.startsWith("[[")) out.push(<code key={k++}>{t.slice(2, -2)}</code>);
    else out.push(<i key={k++}>{t.slice(1, -1)}</i>);
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) blocks.push(<ul key={blocks.length}>{list.map((l, i) => <li key={i}>{inline(l)}</li>)}</ul>);
    list = [];
  };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    if (/^[-*] /.test(line)) { list.push(line.slice(2)); continue; }
    flush();
    if (!line.trim()) continue;
    if (line.startsWith("## ")) blocks.push(<h2 key={blocks.length}>{inline(line.slice(3))}</h2>);
    else if (line.startsWith("# ")) blocks.push(<h1 key={blocks.length}>{inline(line.slice(2))}</h1>);
    else blocks.push(<p key={blocks.length}>{inline(line)}</p>);
  }
  flush();
  return <div className="md">{blocks}</div>;
}
