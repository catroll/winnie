/**
 * Render Markdown without trusting it.
 *
 * Drafts and chat replies are model output written from untrusted sources, so
 * the text is treated as data: `marked` only *parses* it, and the resulting
 * tokens are turned into React elements. Nothing is ever inserted with
 * `dangerouslySetInnerHTML`, so an injected `<script>` or `onerror=` survives
 * only as visible text.
 *
 * The renderer also drops two things a reply never needs and an attacker would
 * like: images are not loaded (a remote pixel would report the reader's IP) and
 * link targets are limited to http(s), mailto and relative URLs.
 *
 * Headings are shifted down two levels because a page owns `h1` and its card
 * title `h2`; a document's `# title` becomes an `h3`, which keeps its hierarchy
 * without a second page heading.
 *
 * `breaks` controls what a single newline means. A news draft is a document,
 * where a soft wrap is just whitespace, so it is off there. A chat reply is
 * typed like a message and its author expects each line to stay a line, so the
 * conversation passes `breaks` and a newline becomes a hard break.
 */

import { useMemo } from 'react';
import type { ReactNode } from 'react';
import { marked } from 'marked';
import type { Token, Tokens } from 'marked';

const HREF_SCHEME = /^(https?:\/\/|mailto:)/i;

function safeHref(value: string | null | undefined): string | null {
  const href = (value ?? '').trim();
  if (!href) {
    return null;
  }
  if (href.startsWith('/') || href.startsWith('#')) {
    return href;
  }
  return HREF_SCHEME.test(href) ? href : null;
}

/**
 * A GFM task item's marker. It is a real (disabled) input so it reads as a
 * checkbox, not as the literal "[x]" the token carries.
 *
 * The token arrives in block position — a sibling of the item's text token
 * inside `list_item.tokens`, not inside its inline tokens — so both walkers
 * have to recognise it. Handling only one drops the marker silently.
 */
function taskCheckbox(box: Tokens.Checkbox, key: string): ReactNode {
  return (
    <input
      key={key}
      type="checkbox"
      className="form-check-input me-1"
      checked={box.checked}
      disabled
      readOnly
    />
  );
}

function inline(tokens: Token[] | undefined, keyPrefix: string): ReactNode {
  if (!tokens || tokens.length === 0) {
    return null;
  }
  return (
    <>
      {tokens.map((token, index) => {
        const key = `${keyPrefix}-${index}`;
        switch (token.type) {
          case 'text':
          case 'escape':
            return <span key={key}>{(token as Tokens.Text).text}</span>;
          case 'strong':
            return <strong key={key}>{inline((token as Tokens.Strong).tokens, key)}</strong>;
          case 'em':
            return <em key={key}>{inline((token as Tokens.Em).tokens, key)}</em>;
          case 'del':
            return <del key={key}>{inline((token as Tokens.Del).tokens, key)}</del>;
          case 'codespan':
            return (
              <code key={key}>{(token as Tokens.Codespan).text}</code>
            );
          case 'br':
            return <br key={key} />;
          case 'checkbox':
            return taskCheckbox(token as Tokens.Checkbox, key);
          case 'link': {
            const link = token as Tokens.Link;
            const label = inline(link.tokens, key) ?? link.text;
            const href = safeHref(link.href);
            return href ? (
              <a key={key} href={href} target="_blank" rel="noopener noreferrer">
                {label}
              </a>
            ) : (
              <span key={key}>{label}</span>
            );
          }
          case 'image': {
            const image = token as Tokens.Image;
            return (
              <span key={key} className="text-secondary">
                [image: {image.text || 'untitled'}]
              </span>
            );
          }
          case 'autolink': {
            const auto = token as Tokens.Link;
            const href = safeHref(auto.href);
            return href ? (
              <a key={key} href={href} target="_blank" rel="noopener noreferrer">
                {auto.text}
              </a>
            ) : (
              <span key={key}>{auto.text}</span>
            );
          }
          case 'html':
            // Raw HTML is shown as text, never as markup.
            return (
              <span key={key} className="text-secondary">
                {(token as Tokens.HTML).text}
              </span>
            );
          default:
            return (
              <span key={key}>{(token as { raw?: string }).raw ?? null}</span>
            );
        }
      })}
    </>
  );
}

function blocks(tokens: Token[], keyPrefix: string): ReactNode {
  return <>{tokens.map((token, index) => block(token, `${keyPrefix}-${index}`))}</>;
}

function block(token: Token, key: string): ReactNode {
  switch (token.type) {
    case 'space':
      return null;
    case 'heading': {
      const heading = token as Tokens.Heading;
      const children = inline(heading.tokens, key);
      const depth = Math.min(heading.depth + 2, 6);
      switch (depth) {
        case 3:
          return <h3 key={key}>{children}</h3>;
        case 4:
          return <h4 key={key}>{children}</h4>;
        case 5:
          return <h5 key={key}>{children}</h5>;
        default:
          return <h6 key={key}>{children}</h6>;
      }
    }
    case 'paragraph':
      return <p key={key}>{inline((token as Tokens.Paragraph).tokens, key)}</p>;
    case 'checkbox':
      return taskCheckbox(token as Tokens.Checkbox, key);
    case 'text':
      return <span key={key}>{inline((token as Tokens.Text).tokens, key)}</span>;
    case 'list': {
      const list = token as Tokens.List;
      const items = list.items.map((item, index) => (
        <li key={`${key}-${index}`}>{blocks(item.tokens, `${key}-${index}`)}</li>
      ));
      return list.ordered ? (
        <ol key={key} start={typeof list.start === 'number' ? list.start : undefined}>
          {items}
        </ol>
      ) : (
        <ul key={key}>{items}</ul>
      );
    }
    case 'blockquote':
      return (
        <blockquote key={key} className="blockquote">
          {blocks((token as Tokens.Blockquote).tokens, key)}
        </blockquote>
      );
    case 'code':
      return (
        <pre key={key}>
          <code>{(token as Tokens.Code).text}</code>
        </pre>
      );
    case 'hr':
      return <hr key={key} />;
    case 'table': {
      const table = token as Tokens.Table;
      return (
        // Bootstrap's table needs its own scroll box, or a wide table pushes
        // the page sideways instead of scrolling within the card.
        <div key={key} className="table-responsive mb-3">
          <table className="table table-sm table-bordered mb-0">
            <thead>
              <tr>
                {table.header.map((cell, index) => (
                  <th
                    key={`${key}-h-${index}`}
                    scope="col"
                    style={{ textAlign: cell.align ?? undefined }}
                  >
                    {inline(cell.tokens, `${key}-h-${index}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((row, rowIndex) => (
                <tr key={`${key}-r-${rowIndex}`}>
                  {row.map((cell, cellIndex) => (
                    <td
                      key={`${key}-r-${rowIndex}-${cellIndex}`}
                      style={{ textAlign: cell.align ?? undefined }}
                    >
                      {inline(cell.tokens, `${key}-r-${rowIndex}-${cellIndex}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
    case 'html':
      // A raw HTML block is data: show the source text, never parse it.
      return (
        <p key={key} className="text-secondary font-monospace small">
          {(token as Tokens.HTML).text}
        </p>
      );
    case 'def':
      return null;
    default:
      return null;
  }
}

export function Markdown({
  source,
  breaks = false,
}: {
  source: string;
  /** Treat a single newline as a hard break. Conversation replies want this. */
  breaks?: boolean;
}) {
  const tokens = useMemo(() => {
    try {
      return marked.lexer(source, { gfm: true, breaks });
    } catch {
      return null;
    }
  }, [source, breaks]);

  if (!tokens) {
    return (
      <pre className="mb-0" style={{ whiteSpace: 'pre-wrap' }}>
        {source}
      </pre>
    );
  }
  return <div className="markdown-body">{blocks(tokens, 'md')}</div>;
}
