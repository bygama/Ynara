"use client";

import ReactMarkdown from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";

const ALLOWED_TAGS = [
  "a",
  "blockquote",
  "br",
  "code",
  "del",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "hr",
  "li",
  "ol",
  "p",
  "pre",
  "strong",
  "ul",
];

/**
 * Schema acotado para `rehype-sanitize`: permite únicamente el subset de texto
 * enriquecido y enlaces HTTP(S)/mailto que emite el asistente, excluyendo
 * elementos embebidos o multimedia (`img`, `iframe`, `svg`, etc.).
 */
export const CHAT_MARKDOWN_SCHEMA = {
  ...defaultSchema,
  tagNames: ALLOWED_TAGS,
  attributes: {
    a: ["href"],
    code: ["className"],
  },
  protocols: {
    href: ["http", "https", "mailto"],
  },
};

const DISALLOWED_ELEMENTS = ["img", "picture", "source", "iframe", "video", "audio", "svg"];

type Props = {
  children: string;
};

export function Markdown({ children }: Props) {
  return (
    <div className="chat-markdown flex flex-col gap-2">
      <ReactMarkdown
        rehypePlugins={[[rehypeSanitize, CHAT_MARKDOWN_SCHEMA]]}
        disallowedElements={DISALLOWED_ELEMENTS}
        components={{
          a: ({ children: linkChildren, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[var(--color-accent)] underline underline-offset-2"
            >
              {linkChildren}
            </a>
          ),
          code: ({ children: codeChildren, className }) => (
            <code
              className={
                className
                  ? "block overflow-x-auto rounded-[var(--radius-sm)] bg-[var(--color-bg-soft)] p-3 text-body-sm"
                  : "rounded-[var(--radius-sm)] bg-[var(--color-bg-soft)] px-1.5 py-0.5 text-body-sm"
              }
            >
              {codeChildren}
            </code>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
