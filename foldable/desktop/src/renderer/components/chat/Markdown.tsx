/**
 * Assistant-answer renderer: react-markdown + GFM + highlight.js.
 * No raw HTML is ever rendered (no rehype-raw). Links become buttons that
 * open through the allowlisted openExternal bridge, otherwise plain text.
 */
import { memo, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import { isAllowedExternalUrl } from "../../../shared-types/bridge";
import CodeBlock from "./CodeBlock";

interface HastNode {
  type: string;
  value?: string;
  tagName?: string;
  properties?: { className?: string[] | string };
  children?: HastNode[];
}

function hastText(node: HastNode | undefined): string {
  if (!node) return "";
  if (node.type === "text") return node.value ?? "";
  return (node.children ?? []).map(hastText).join("");
}

function codeLanguage(pre: HastNode | undefined): string | null {
  const code = pre?.children?.find((c) => c.type === "element" && c.tagName === "code");
  const cls = code?.properties?.className;
  const list = Array.isArray(cls) ? cls : typeof cls === "string" ? cls.split(" ") : [];
  const lang = list.find((c) => c.startsWith("language-"));
  return lang ? lang.slice("language-".length) : null;
}

function openLink(url: string): void {
  void window.spaider?.openExternal(url);
}

const remarkPlugins = [remarkGfm];
const rehypePlugins = [[rehypeHighlight, { detect: false }] as const];

const components: Components = {
  a: ({ href, children }) => {
    const url = typeof href === "string" ? href : "";
    if (url && isAllowedExternalUrl(url)) {
      return (
        <button type="button" className="md-link" title={url} onClick={() => openLink(url)}>
          {children}
        </button>
      );
    }
    return (
      <span className="text-text-secondary" title={url || undefined}>
        {children}
        {url ? <span className="text-text-secondary/70"> ({url})</span> : null}
      </span>
    );
  },
  pre: ({ node, children }) => {
    const hast = node as unknown as HastNode | undefined;
    return (
      <CodeBlock language={codeLanguage(hast)} text={hastText(hast).replace(/\n$/, "")}>
        {children as ReactNode}
      </CodeBlock>
    );
  },
  img: ({ alt }) => <span className="text-text-secondary">[Bild: {alt || "ohne Beschreibung"}]</span>,
  input: ({ checked }) => (
    <input type="checkbox" checked={Boolean(checked)} readOnly disabled aria-label="Aufgabe" />
  ),
};

function MarkdownInner({ content, className }: { content: string; className?: string }) {
  return (
    <div className={className ? `markdown ${className}` : "markdown"}>
      <ReactMarkdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins as never} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

const Markdown = memo(MarkdownInner);
export default Markdown;
