import type { NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState } from "react";

import { sendOnEnter } from "../lib/hooks";
import { withCitations } from "../lib/text";
import { accent, RELATION_STYLE } from "../relations";
import { useGraph } from "../store/graphs";
import type { AnswerNode as AnswerNodeType } from "../types";
import { Handles, RemoveButton, Rotated } from "./parts";

/** An LLM answer on the canvas. It's placed when Ask is clicked, as a question box (draft); once asked, the
 *  question becomes its title and the answer fills in below. Its citation chips add the cited sources. */
export function AnswerNode({ id, data, selected }: NodeProps<AnswerNodeType>) {
    const addCited = useGraph(s => s.addCited);
    const submit = useGraph(s => s.submitQuestion);
    const updateAnswer = useGraph(s => s.updateAnswer);
    const removeNode = useGraph(s => s.removeNode);
    const editing = data.status === "draft" || data.status === "error";
    const cited = data.sources.filter(s => s.cited);

    return (
        <Rotated deg={data.rotation}>
            <div className={`passage-node kind-answer${selected ? " selected" : ""}`} style={accent(RELATION_STYLE.answer.colour)}>
                <Handles />
                <header>
                    <span className="kind">{data.status === "ready" ? `answer · ${data.model}` : data.about ? "ask" : "ask the corpus"}</span>
                    {selected && <RemoveButton id={id} />}
                </header>

                {data.selection && <blockquote className="ask-selection"><mark>{data.selection}</mark></blockquote>}

                {editing
                    ? <QuestionBox
                        initial={data.question}
                        error={data.status === "error" ? data.error : null}
                        onSend={q => submit(id, q)}
                        onLeave={q => q.trim() ? updateAnswer(id, { question: q }) : removeNode(id)}
                    />
                    : <div className="title">{data.question}</div>}

                {data.status === "loading" && <div className="ask-status">Reading the arguments, passages and issues…</div>}

                {data.status === "ready" && <>
                    <p className={`text${selected ? " nowheel nodrag" : ""}`}>
                        {withCitations(data.answer, data.sources, s => addCited(id, s))}
                    </p>
                    {!data.found &&
                        <div className="notice">{cited.length ? "The material may not answer this." : "No sources cited: the material doesn't seem to answer this."}</div>}
                    {cited.length > 0 && selected &&
                        <details className="quote nodrag nowheel">
                            <summary>Cited ({cited.length})</summary>
                            {cited.map(s => (
                                <button key={s.id} className="cited-source" onClick={() => addCited(id, s)} title="Add it to the canvas">
                                    [{s.label}] {s.title}{s.holder ? ` (${s.holder.name})` : ""}
                                </button>
                            ))}
                        </details>}
                    <div className="provenance">Written by {data.model} from the cited sources; check them.</div>
                </>}
            </div>
        </Rotated>
    );
}

/** The question being drafted. Enter (or the button) sends; leaving it empty removes the node, like a note. */
function QuestionBox({ initial, error, onSend, onLeave }: {
    initial: string,
    error: string | null,
    onSend: (question: string) => void,
    onLeave: (question: string) => void,
}) {
    const [question, setQuestion] = useState(initial);
    const ref = useRef<HTMLTextAreaElement>(null);
    useEffect(() => { ref.current?.focus({ preventScroll: true }); }, []);
    const send = () => { if (question.trim()) onSend(question); };

    return (
        <div className="ask-box nodrag nowheel">
            <textarea
                ref={ref}
                className="ask-input"
                rows={2}
                placeholder="Ask a question about this…"
                value={question}
                onChange={e => setQuestion(e.target.value)}
                onKeyDown={sendOnEnter(send)}
                // Switching window or tab blurs too: that isn't leaving the node.
                onBlur={e => { if (document.hasFocus()) onLeave(e.target.value); }}
            />
            <div className="ask-row">
                <span className="count">{error && <span className="error">{error}</span>}</span>
                {/* preventDefault on mousedown keeps focus in the box, so an empty one isn't read as left */}
                <button className="ask-send" disabled={!question.trim()} onMouseDown={e => e.preventDefault()} onClick={send}>
                    {error ? "Try again" : "Ask"}
                </button>
            </div>
        </div>
    );
}
