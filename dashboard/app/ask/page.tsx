"use client";

import { useState } from "react";
import { Search, Loader2 } from "lucide-react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { askGraph, type AskResult } from "@/lib/api";

const EXAMPLES = [
  "Which startups raised the largest funding rounds recently?",
  "What competitive risks are facing AI music companies?",
  "Which companies are hiring aggressively after a raise?",
  "Summarize the most significant funding signals this week.",
];

const mdComponents: Components = {
  h1: ({ children }) => <h1 className="mb-2 mt-4 text-lg font-semibold text-gray-900">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-2 mt-4 text-base font-semibold text-gray-900">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1.5 mt-3 text-sm font-semibold text-gray-900">{children}</h3>,
  p: ({ children }) => <p className="my-2 text-sm leading-relaxed text-gray-800">{children}</p>,
  ul: ({ children }) => <ul className="my-2 ml-5 list-disc space-y-1 text-sm text-gray-800">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 ml-5 list-decimal space-y-1 text-sm text-gray-800">{children}</ol>,
  li: ({ children }) => <li className="leading-relaxed">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-gray-900">{children}</strong>,
  a: ({ href, children }) => (
    <a href={href} className="text-blue-600 underline" target="_blank" rel="noreferrer">{children}</a>
  ),
  code: ({ children }) => (
    <code className="rounded bg-gray-100 px-1 py-0.5 font-mono text-xs text-gray-700">{children}</code>
  ),
  table: ({ children }) => (
    <div className="my-3 overflow-x-auto">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border px-3 py-1.5 text-left font-semibold text-gray-700">{children}</th>
  ),
  td: ({ children }) => <td className="border px-3 py-1.5 text-gray-800">{children}</td>,
};

export default function AskPage() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AskResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(q: string) {
    const query = q.trim();
    if (!query || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await askGraph(query));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Ask the Knowledge Graph</h1>
        <p className="mt-1 text-sm text-gray-600">
          Natural-language questions answered from the GenAI-Intel brain — companies, signals,
          funding, and the relationships between them. Every answer cites the graph pages it drew from.
        </p>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          run(question);
        }}
        className="flex gap-2"
      >
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. Which AWS startups raised over $100M and who do they compete with?"
          className="flex-1 rounded-lg border bg-white px-4 py-2.5 text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-gray-300"
        />
        <button
          type="submit"
          disabled={loading || !question.trim()}
          className="inline-flex items-center gap-2 rounded-lg bg-gray-900 px-4 py-2.5 text-sm font-medium text-white transition-opacity disabled:opacity-50"
        >
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
          Ask
        </button>
      </form>

      {!result && !loading && !error && (
        <div className="flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => {
                setQuestion(ex);
                run(ex);
              }}
              className="rounded-full border bg-white px-3 py-1.5 text-xs text-gray-600 transition-colors hover:border-gray-400 hover:text-gray-900"
            >
              {ex}
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {loading && (
        <div className="flex items-center gap-2 text-sm text-gray-500">
          <Loader2 className="h-4 w-4 animate-spin" /> Searching the graph and synthesizing an answer…
        </div>
      )}

      {result && (
        <div className="space-y-4">
          <div className="rounded-xl border bg-white p-5 shadow-sm">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
              {result.answer}
            </ReactMarkdown>
          </div>
          {result.sources.length > 0 && (
            <div className="rounded-xl border bg-white p-5 shadow-sm">
              <h2 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500">
                Sources ({result.sources.length}) — pages retrieved from the graph
              </h2>
              <ul className="space-y-1.5">
                {result.sources.map((s) => (
                  <li key={s.slug} className="flex items-center gap-2 text-sm">
                    <span className="w-10 font-mono text-xs text-gray-400">
                      {s.score?.toFixed(2)}
                    </span>
                    <span className="font-mono text-xs text-gray-700">{s.slug}</span>
                    {s.title && <span className="truncate text-gray-500">— {s.title}</span>}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
