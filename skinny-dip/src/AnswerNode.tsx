import { Handle, type NodeProps } from "@xyflow/react";
import type { CSSProperties } from "react";

import { RELATION_STYLE } from "./relations";
import { useGraph, type AnswerNode as AnswerNodeType, type QuestionNode as QuestionNodeType } from "./store/graphs";
import { SIDES, withCitations } from "./utils";

/** An LLM answer on the canvas. Its citation chips add the cited sources, linked from it. */
export function AnswerNode({ id, data, selected }: NodeProps<AnswerNodeType>) {
    const removeNode = useGraph(s => s.removeNode);
    const addCited = useGraph(s => s.addCited);
    const cited = data.sources.filter(s => s.cited);

    return (
        <div className="rotation-container" style={{ transform: `rotate(${data.rotation}deg)` }}>
            <div className={`passage-node kind-answer${selected ? " selected" : ""}`}
                style={{ "--c": RELATION_STYLE.answer.colour } as CSSProperties}>
                {SIDES.map(([side, pos]) => <Handle key={`s-${side}`} id={`s-${side}`} type="source" position={pos} isConnectable={false} />)}
                {SIDES.map(([side, pos]) => <Handle key={`t-${side}`} id={`t-${side}`} type="target" position={pos} isConnectable={false} />)}

                <header>
                    <span className="kind">answer · {data.model}</span>
                    {selected && <button className="remove nodrag" title="Remove from canvas" onClick={() => removeNode(id)}>×</button>}
                </header>
                <div className="title">{data.question}</div>
                {data.selection && <blockquote className="ask-selection"><mark>{data.selection}</mark></blockquote>}
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
            </div>
        </div>
    );
}

/** A question about the whole corpus: the seed its answer stems from. */
export function QuestionNode({ id, data, selected }: NodeProps<QuestionNodeType>) {
    const removeNode = useGraph(s => s.removeNode);
    return (
        <div className="rotation-container" style={{ transform: `rotate(${data.rotation}deg)` }}>
            <div className={`passage-node seed kind-question${selected ? " selected" : ""}`}>
                {SIDES.map(([side, pos]) => <Handle key={`s-${side}`} id={`s-${side}`} type="source" position={pos} isConnectable={false} />)}
                {SIDES.map(([side, pos]) => <Handle key={`t-${side}`} id={`t-${side}`} type="target" position={pos} isConnectable={false} />)}
                {selected && <button className="remove nodrag" title="Remove from canvas" onClick={() => removeNode(id)}>×</button>}
                <p className="text">{data.question}</p>
            </div>
        </div>
    );
}
