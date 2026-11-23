import { NodeToolbar, Position, type NodeProps } from "@xyflow/react";
import { useEffect, useRef, useState, type RefObject } from "react";

import { MOVES } from "../api";
import { seedTextId } from "../lib/canvas";
import { VOICE_LABEL, withHighlight } from "../lib/text";
import { accent, RELATION_STYLE } from "../relations";
import { useGraph } from "../store/graphs";
import type { PassageData, PassageNode as PassageNodeType } from "../types";
import { Handles, MovesToolbar, RemoveButton, Rotated } from "./parts";

/** A DIP node as a card. A seed shows as a title card (`isSeedTitle`) that can expand a description card
 *  (`isSeedText`); both carry the same DIP node, so moves and Ask work the same on either. */
export function PassageNode({ id, data, selected }: NodeProps<PassageNodeType>) {
    const openAsk = useGraph(s => s.openAsk);
    const textRef = useRef<HTMLParagraphElement>(null);
    const selection = useTextSelection(textRef, selected);
    const verbatim = data.provenance === "verbatim";

    return (
        <Rotated deg={data.rotation}>
            <div className={`passage-node kind-${data.kind}${verbatim ? " verbatim" : ""}${selected ? " selected" : ""}${data.isSeed ? " seed" : ""}`}>
                <Handles />
                {selected && <RemoveButton id={id} />}
                {data.isSeedTitle ? <h1>{data.title}</h1> : <CardBody data={data} selected={selected} textRef={textRef} />}
            </div>

            {selection.length > 2 &&
                <NodeToolbar position={Position.Top} align="center">
                    <button className="ask-selection-button nodrag" style={accent(RELATION_STYLE.answer.colour)}
                        onMouseDown={e => e.preventDefault() /* keep the text selected */}
                        onClick={() => openAsk(id, selection)}
                        title={selection}>
                        Ask about “{selection.length > 40 ? selection.slice(0, 40) + "…" : selection}”
                    </button>
                </NodeToolbar>}

            {data.isSeedTitle && <DescriptionToggle id={id} dipId={data.dipId} />}

            <MovesToolbar id={id} moves={MOVES[data.kind] ?? []} pending={data.pending}>
                <button title={RELATION_STYLE.answer.hint} style={accent(RELATION_STYLE.answer.colour)} onClick={() => openAsk(id, null)}>
                    Ask
                </button>
            </MovesToolbar>
        </Rotated>
    );
}

/** Text selected inside `ref` while the card is selected (only then is its text selectable); "" otherwise. */
function useTextSelection(ref: RefObject<HTMLElement | null>, selected: boolean) {
    const [picked, setPicked] = useState("");
    useEffect(() => {
        if (!selected) return;
        const onChange = () => {
            const sel = window.getSelection();
            const inside = !!sel && !sel.isCollapsed && !!ref.current?.contains(sel.anchorNode);
            setPicked(inside ? sel.toString().trim() : "");
        };
        onChange();
        document.addEventListener("selectionchange", onChange);
        return () => document.removeEventListener("selectionchange", onChange);
    }, [ref, selected]);
    return selected ? picked : "";
}

/** The +/− button beside a seed title that shows or hides its description card. */
function DescriptionToggle({ id, dipId }: { id: string, dipId: string }) {
    const toggleSeedText = useGraph(s => s.toggleSeedText);
    const shown = useGraph(s => s.nodes.some(n => n.id === seedTextId(dipId)));
    return (
        <NodeToolbar position={Position.Right} align="center">
            <button className="nodrag toggle-description" title={shown ? "Hide the description" : "Show the description"}
                onClick={() => toggleSeedText(id)}>
                {shown ? "−" : "+"}
            </button>
        </NodeToolbar>
    );
}

/** Everything but a seed title: holder, title, text, reception, quotes and source. */
function CardBody({ data, selected, textRef }: { data: PassageData, selected: boolean, textRef: RefObject<HTMLParagraphElement | null> }) {
    const verbatim = data.provenance === "verbatim";
    const sources = data.sources ?? [];
    return (
        <>
            <header>
                {data.holder && <div className={`holder-chip holder-${data.holder.type}`} title={data.holder.type}>{data.holder.name}</div>}
            </header>
            {!data.isSeedText && <div className="title">{data.title}</div>}
            <p className={`text${selected ? " nowheel nodrag" : ""}`} ref={textRef}>
                {verbatim ? withHighlight(data.text, data.highlight) : data.text}
            </p>

            {data.reception && data.reception.length > 0 &&
                <div className="reception">
                    {data.reception.map((r, i) => (
                        <span key={i} className={`voice voice-${r.voice}`}>{VOICE_LABEL[r.voice] ?? r.voice}{" "}
                            {r.url ? <a className="nodrag" href={r.url} target="_blank" rel="noreferrer">“{r.document}”</a> : `“${r.document}”`}</span>
                    ))}
                </div>}

            {sources.length > 0 && selected &&
                <details className="quote nodrag nowheel">
                    <summary>Quote{sources.length > 1 ? `s (${sources.length})` : ""}</summary>
                    {sources.map((s, i) => (
                        <blockquote key={i} title={s.quote_verified ? "found in the source" : "not found verbatim in the source"}>
                            “{s.quote}”{!s.quote_verified && " ⚠"}
                            <cite>{s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.document}</a> : s.document}</cite>
                        </blockquote>
                    ))}
                </details>}

            {!sources.length && (data.sourceUrl
                ? <a target="_blank" rel="noreferrer" href={data.sourceUrl} className="source nodrag" title={data.sourceUrl}>{data.document ?? data.title}</a>
                : data.document && !verbatim && <span className="source">{data.document}</span>)}

            {data.notice && <div className="notice">{data.notice}</div>}
        </>
    );
}
