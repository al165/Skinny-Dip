import type { NodeProps } from "@xyflow/react";

import { withCitations } from "../lib/text";
import { accent, RELATION_STYLE } from "../relations";
import { useGraph } from "../store/graphs";
import type { AnswerNode as AnswerNodeType } from "../types";
import { Handles, RemoveButton, Rotated } from "./parts";

/** An LLM answer on the canvas. Its citation chips add the cited sources, linked from it. */
export function AnswerNode({ id, data, selected }: NodeProps<AnswerNodeType>) {
    const addCited = useGraph(s => s.addCited);
    const cited = data.sources.filter(s => s.cited);

    return (
        <Rotated deg={data.rotation}>
            <div className={`passage-node kind-answer${selected ? " selected" : ""}`} style={accent(RELATION_STYLE.answer.colour)}>
                <Handles />
                <header>
                    <span className="kind">answer · {data.model}</span>
                    {selected && <RemoveButton id={id} />}
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
        </Rotated>
    );
}
