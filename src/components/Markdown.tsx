import { Fragment, type ReactNode } from 'react';

// Renders the small Markdown subset the legal documents use (headings, paragraphs, lists, tables, block quotes, bold,
// italics, and inline code) as React elements. No HTML from the text is ever inserted into the page.

function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > last) parts.push(text.slice(last, index));
    const token = match[0];
    if (token.startsWith('**')) parts.push(<strong key={index}>{token.slice(2, -2)}</strong>);
    else if (token.startsWith('`')) parts.push(<code key={index}>{token.slice(1, -1)}</code>);
    else parts.push(<em key={index}>{token.slice(1, -1)}</em>);
    last = index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

const cells = (line: string) => line.trim().replace(/^\||\|$/g, '').split('|').map(cell => cell.trim());

export function Markdown({ text }: { text: string }) {
  const lines = text.split('\n');
  const blocks: ReactNode[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    const key = `block-${index}`;
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const content = inline(heading[2]);
      blocks.push(
        level === 1 ? <h1 key={key}>{content}</h1> : level === 2 ? <h2 key={key}>{content}</h2> : level === 3 ? <h3 key={key}>{content}</h3> : <h4 key={key}>{content}</h4>,
      );
      index += 1;
      continue;
    }
    if (line.startsWith('|')) {
      const rows: string[] = [];
      while (index < lines.length && lines[index].startsWith('|')) rows.push(lines[index++]);
      const [header, , ...body] = rows;
      blocks.push(
        <div key={key} style={{ overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '0.9rem' }}>
            <thead>
              <tr>{cells(header).map((cell, i) => <th key={i} style={{ textAlign: 'left', padding: '6px', borderBottom: '1px solid var(--border-strong)' }}>{inline(cell)}</th>)}</tr>
            </thead>
            <tbody>
              {body.map((row, r) => (
                <tr key={r}>{cells(row).map((cell, i) => <td key={i} style={{ padding: '6px', borderBottom: '1px solid var(--border)' }}>{inline(cell)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && (/^\s*[-*]\s+/.test(lines[index]) || (lines[index].startsWith('  ') && items.length))) {
        if (/^\s*[-*]\s+/.test(lines[index])) items.push(lines[index].replace(/^\s*[-*]\s+/, ''));
        else items[items.length - 1] += ` ${lines[index].trim()}`;
        index += 1;
      }
      blocks.push(<ul key={key}>{items.map((item, i) => <li key={i}>{inline(item.replace(/^\[ \]\s*/, ''))}</li>)}</ul>);
      continue;
    }
    if (line.startsWith('>')) {
      const quoted: string[] = [];
      while (index < lines.length && lines[index].startsWith('>')) quoted.push(lines[index++].replace(/^>\s?/, ''));
      blocks.push(
        <blockquote key={key} style={{ borderLeft: '3px solid #9A5B00', margin: '12px 0', padding: '4px 12px', color: 'var(--text-secondary)' }}>
          {inline(quoted.join(' '))}
        </blockquote>,
      );
      continue;
    }
    const paragraph: string[] = [];
    while (index < lines.length && lines[index].trim() && !/^(#{1,4}\s|\||>|\s*[-*]\s)/.test(lines[index])) paragraph.push(lines[index++]);
    blocks.push(<p key={key}>{inline(paragraph.join(' '))}</p>);
  }
  return <Fragment>{blocks}</Fragment>;
}
