import { Panel } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";

import { resultFlowId } from "../lib/canvas";
import { sendOnEnter, useEscape, useQuestionSpot } from "../lib/hooks";
import { accent, RELATION_STYLE } from "../relations";
import { useGraph } from "../store/graphs";
import { isPassage } from "../types";
import { ResultCard } from "./ResultCard";

/** Ask the LLM about a card (and a selection in it). Corpus questions are typed in the seeds panel; this panel
 *  then shows their status and sources. */
export function AskPanel() {
    const ask = useGraph(s => s.ask);
    const source = useGraph(s => s.nodes.find(n => n.id === s.ask?.sourceId));
    const nodes = useGraph(s => s.nodes);
    const submit = useGraph(s => s.submitQuestion);
    const addCited = useGraph(s => s.addCited);
    const close = useGraph(s => s.closeAsk);
    const questionSpot = useQuestionSpot();
    const [question, setQuestion] = useState("");
    const inputRef = useRef<HTMLTextAreaElement>(null);
    useEscape(!!ask, close);

    // A new card or selection: focus the box.
    const askKey = ask ? `${ask.sourceId}|${ask.selection}` : null;
    useEffect(() => {
        if (askKey) inputRef.current?.focus({ preventScroll: true });
    }, [askKey]);

    if (!ask) return null;

    const card = source && isPassage(source) ? source.data : null;
    const r = ask.response;
    const onCanvas = new Set(nodes.map(n => n.id));
    const send = () => { if (question.trim()) submit(question, questionSpot()); };

    const status = (
        <>
            {ask.status === "loading" && "Reading the arguments, passages and issues…"}
            {ask.status === "error" && <span className="error">{ask.error}</span>}
            {ask.status === "ready" && r && `${r.meta.cached ? "cached" : `${r.meta.seconds}s`} · ${r.meta.model}`}
        </>
    );

    return (
        <Panel position="center-right" className="nowheel">
            <div id="results-panel" className="ask-panel" style={accent(RELATION_STYLE.answer.colour)}>
                <div className="panel-head">
                    <div className="results-title">
                        <span className="relation">Ask</span>
                        <button className="close" title="Close (Esc)" onClick={close}>×</button>
                    </div>
                    <div className="results-source" title={card?.text}>
                        {card ? <>about the {card.kind} <b>{card.title}</b></> : "about the whole corpus"}
                    </div>
                    {ask.selection && <blockquote className="ask-selection"><mark>{ask.selection}</mark></blockquote>}
                    {card ? <>
                        <textarea
                            ref={inputRef}
                            className="ask-input"
                            rows={2}
                            placeholder="Ask a question about this…"
                            value={question}
                            onChange={e => setQuestion(e.target.value)}
                            onKeyDown={sendOnEnter(send)}
                        />
                        <div className="ask-row">
                            <span className="count">{status}</span>
                            <button className="ask-send" disabled={ask.status === "loading" || !question.trim()} onClick={send}>
                                {ask.status === "loading" ? "Asking…" : "Ask"}
                            </button>
                        </div>
                    </> : <div className="count">{status}</div>}
                </div>
                {r && ask.status !== "loading" &&
                    <div className="result-list">
                        <div className="ask-answer">
                            <div className="result-meta"><span>{r.question}</span></div>
                            <div className="ask-answer-foot">
                                <span>{ask.answerId ? "The answer is on the canvas. Add the material it drew on:" : "The answer was removed from the canvas; ask again to bring it back."}</span>
                            </div>
                        </div>
                        {r.sources.map(s => (
                            <ResultCard key={s.id} result={s} className={s.cited ? "" : "uncited"} disabled={!ask.answerId}
                                onClick={() => { if (ask.answerId) addCited(ask.answerId, s); }}
                                label={s.label}
                                state={onCanvas.has(resultFlowId(s)) ? "on canvas" : s.cited ? "cited" : "not cited"}
                            />
                        ))}
                    </div>}
            </div>
        </Panel>
    );
}
