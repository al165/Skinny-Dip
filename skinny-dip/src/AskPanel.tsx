import { Panel, useReactFlow } from "@xyflow/react";
import { useEffect, useRef, useState, type CSSProperties } from "react";

import type { QuestionSource } from "./api";
import { RELATION_STYLE } from "./relations";
import { isPassage, useGraph } from "./store/graphs";
import { resultFlowId } from "./utils";

/** Ask the LLM about a card (and a selection in it), or about the whole corpus. */
export function AskPanel() {
    const ask = useGraph(s => s.ask);
    const source = useGraph(s => s.nodes.find(n => n.id === s.ask?.sourceId));
    const nodes = useGraph(s => s.nodes);
    const submit = useGraph(s => s.submitQuestion);
    const addCited = useGraph(s => s.addCited);
    const close = useGraph(s => s.closeAsk);
    const { screenToFlowPosition } = useReactFlow();
    const [question, setQuestion] = useState("");
    const inputRef = useRef<HTMLTextAreaElement>(null);

    // A new card or selection: focus the box. Escape closes the panel.
    const askKey = ask ? `${ask.sourceId}|${ask.selection}` : null;
    useEffect(() => {
        if (!askKey) return;
        inputRef.current?.focus({ preventScroll: true });
        const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
        window.addEventListener("keydown", onKey);
        return () => window.removeEventListener("keydown", onKey);
    }, [askKey, close]);

    if (!ask) return null;

    const style = RELATION_STYLE.answer;
    const card = source && isPassage(source) ? source.data : null;
    const r = ask.response;
    const onCanvas = new Set(nodes.map(n => n.id));
    // Where a corpus question's node goes: the middle of the screen.
    const centre = () => screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 3 });
    const add = (s: QuestionSource) => { if (ask.answerId) addCited(ask.answerId, s); };
    const send = () => { if (question.trim()) submit(question, centre()); };

    return (
        <Panel position="center-right" className="nowheel">
            <div id="results-panel" className="ask-panel" style={{ "--c": style.colour } as CSSProperties}>
                <div className="panel-head">
                    <div className="results-title">
                        <span className="relation">Ask</span>
                        <button className="close" title="Close (Esc)" onClick={close}>×</button>
                    </div>
                    <div className="results-source" title={card?.text}>
                        {card ? <>about the {card.kind} <b>{card.title}</b></> : "about the whole corpus"}
                    </div>
                    {ask.selection && <blockquote className="ask-selection"><mark>{ask.selection}</mark></blockquote>}
                    {card && <>
                    <textarea
                        ref={inputRef}
                        className="ask-input"
                        rows={2}
                        placeholder={card ? "Ask a question about this…" : "Ask a question about the corpus…"}
                        value={question}
                        onChange={e => setQuestion(e.target.value)}
                        onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                    />
                    <div className="ask-row">
                        <span className="count">
                            {ask.status === "loading" && "Reading the arguments, passages and issues…"}
                            {ask.status === "error" && <span className="error">{ask.error}</span>}
                            {ask.status === "ready" && r && `${r.meta.cached ? "cached" : `${r.meta.seconds}s`} · ${r.meta.model}`}
                        </span>
                        <button className="ask-send" disabled={ask.status === "loading" || !question.trim()} onClick={send}>
                            {ask.status === "loading" ? "Asking…" : "Ask"}
                        </button>
                    </div>
                    </>}
                    {!card && <div className="count">
                        {ask.status === "loading" && "Reading the arguments, passages and issues…"}
                        {ask.status === "error" && <span className="error">{ask.error}</span>}
                        {ask.status === "ready" && r && `${r.meta.cached ? "cached" : `${r.meta.seconds}s`} · ${r.meta.model}`}
                    </div>}
                </div>
                {r && ask.status !== "loading" &&
                    <div className="result-list">
                        <div className="ask-answer">
                            <div className="result-meta"><span>{r.question}</span></div>
                            <div className="ask-answer-foot">
                                <span>{ask.answerId ? "The answer is on the canvas. Add the material it drew on:" : "The answer was removed from the canvas; ask again to bring it back."}</span>
                            </div>
                        </div>
                        {r.sources.map(s => {
                            const id = resultFlowId(s);
                            const here = onCanvas.has(id);
                            return (
                                <button key={s.id} className={`result${s.cited ? "" : " uncited"}`} onClick={() => add(s)} title={s.why} disabled={!ask.answerId}>
                                    <div className="result-meta">
                                        <span>{s.label} · {s.kind}{s.role ? ` · ${s.role}` : ""}{s.holder ? ` · ${s.holder.name}` : ""}</span>
                                        <span>{here ? "on canvas" : s.cited ? "cited" : "not cited"}</span>
                                    </div>
                                    <div className="result-title">{s.title}</div>
                                    <div className={`result-text${s.provenance === "verbatim" ? " verbatim" : ""}`}>{s.text}</div>
                                    {s.why && <div className="result-why">{s.why}</div>}
                                    {s.document && <div className="result-doc">{s.document}</div>}
                                </button>
                            );
                        })}
                    </div>}
            </div>
        </Panel>
    );
}
