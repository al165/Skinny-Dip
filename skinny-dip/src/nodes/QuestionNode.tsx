import type { NodeProps } from "@xyflow/react";

import type { QuestionNode as QuestionNodeType } from "../types";
import { Handles, RemoveButton, Rotated } from "./parts";

/** A question about the whole corpus: the seed its answer stems from. */
export function QuestionNode({ id, data, selected }: NodeProps<QuestionNodeType>) {
    return (
        <Rotated deg={data.rotation}>
            <div className={`passage-node seed kind-question${selected ? " selected" : ""}`}>
                <Handles />
                {selected && <RemoveButton id={id} />}
                <p className="text">{data.question}</p>
            </div>
        </Rotated>
    );
}
