import { Handle, Position, NodeToolbar, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState, type CSSProperties } from "react";

import { MOVES } from "./api";
import { RELATION_STYLE } from "./relations";
import { useGraph, type PassageNode as PassageNodeType } from "./store/graphs";
import { SIDES, VOICE_LABEL, withHighlight } from "./utils";

export function PassageNode({ id, data, selected }: NodeProps<PassageNodeType>) {
    const expand = useGraph(s => s.expand);
    const removeNode = useGraph(s => s.removeNode);
    const openAsk = useGraph(s => s.openAsk);
    // Text selected in this card (only possible while it is selected): offers "Ask about this".
    const textRef = useRef<HTMLParagraphElement>(null);
    const [picked, setPicked] = useState("");
    const selection = selected ? picked : "";
    useEffect(() => {
        if (!selected) return;
        const onChange = () => {
            const sel = window.getSelection();
            const inside = !!sel && !sel.isCollapsed && !!textRef.current?.contains(sel.anchorNode);
            setPicked(inside ? sel.toString().trim() : "");
        };
        onChange();
        document.addEventListener("selectionchange", onChange);
        return () => document.removeEventListener("selectionchange", onChange);
    }, [selected]);
    const moves = MOVES[data.kind] ?? [];
    const verbatim = data.provenance === "verbatim";
    const firstQuote = data.sources?.[0];

    return (
        <>
            <div className="rotation-container" style={{ transform: `rotate(${data.rotation}deg)` }}>
                <div className={`passage-node kind-${data.kind}${verbatim ? " verbatim" : ""}${selected ? " selected" : ""} ${data.isSeed ? "seed" : ""}`}>
                    {SIDES.map(([side, pos]) => (
                        <Handle key={`s-${side}`} id={`s-${side}`} type="source" position={pos} isConnectable={false} />
                    ))}
                    {SIDES.map(([side, pos]) => (
                        <Handle key={`t-${side}`} id={`t-${side}`} type="target" position={pos} isConnectable={false} />
                    ))}

                    {selected && <button className="remove nodrag" title="Remove from canvas" onClick={() => removeNode(id)}>×</button>}

                    <header>
                        {data.holder && <div className={`holder-chip holder-${data.holder.type}`} title={data.holder.type}>{data.holder.name}</div>}
                    </header>

                    <div className="title">{data.title}</div>
                    <p className={`text${selected ? " nowheel nodrag" : ""}`} ref={textRef}>{verbatim ? withHighlight(data.text, data.highlight) : data.text}</p>

                    {/* <div className="provenance">{provenanceLabel(data)}</div> */}

                    {data.reception && data.reception.length > 0 &&
                        <div className="reception">
                            {data.reception.map((r, i) => (
                                <span key={i} className={`voice voice-${r.voice}`}>{VOICE_LABEL[r.voice] ?? r.voice}{" "}
                                    {r.url ? <a className="nodrag" href={r.url} target="_blank" rel="noreferrer">“{r.document}”</a> : `“${r.document}”`}</span>
                            ))}
                        </div>}

                    {firstQuote && selected &&
                        <details className="quote nodrag nowheel">
                            <summary>Quote{data.sources && data.sources.length > 1 ? `s (${data.sources.length})` : ""}</summary>
                            {data.sources!.map((s, i) => (
                                <blockquote key={i} title={s.quote_verified ? "found in the source" : "not found verbatim in the source"}>
                                    “{s.quote}”{!s.quote_verified && " ⚠"}
                                    <cite>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.document}</a> : s.document}</cite>
                                </blockquote>
                            ))}
                        </details>}

                    {!data.sources?.length && (data.sourceUrl
                        ? <a target="_blank" rel="noreferrer" href={data.sourceUrl} className="source nodrag" title={data.sourceUrl}>{data.document ?? data.title}</a>
                        : data.document && !verbatim && <span className="source">{data.document}</span>)}

                    {data.notice && <div className="notice">{data.notice}</div>}
                </div>
                {selection.length > 2 &&
                    <NodeToolbar position={Position.Top} align="center">
                        <button className="ask-selection-button nodrag" style={{ "--c": RELATION_STYLE.answer.colour } as CSSProperties}
                            onMouseDown={e => e.preventDefault() /* keep the text selected */}
                            onClick={() => openAsk(id, selection)}
                            title={selection}>
                            Ask about “{selection.length > 40 ? selection.slice(0, 40) + "…" : selection}”
                        </button>
                    </NodeToolbar>}
                <NodeToolbar position={Position.Bottom} align="center">
                    <div className="actions nodrag">
                        {moves.map(r => {
                            const style = RELATION_STYLE[r];
                            return (
                                <button
                                    key={r}
                                    title={style.hint}
                                    disabled={!!data.pending}
                                    className={data.pending === r ? "pending" : ""}
                                    style={{ "--c": style.colour } as CSSProperties}
                                    onClick={() => expand(id, r)}
                                >
                                    {style.label}
                                </button>
                            );
                        })}
                        <button
                            title={RELATION_STYLE.answer.hint}
                            style={{ "--c": RELATION_STYLE.answer.colour } as CSSProperties}
                            onClick={() => openAsk(id, null)}
                        >
                            Ask
                        </button>
                    </div>
                </NodeToolbar>
            </div>
        </>
    );
}
