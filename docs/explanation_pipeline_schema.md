# Explanation Pipeline Schema

## Core Intent

`ExplanationIntent`

```ts
type ExplanationIntent = {
  rawQuestion: string;
  normalizedQuestion: string;
  mode: "simple_explain" | "brief_explain" | "repeat_previous" | "visualize";
  wantsVisuals: boolean;
  wantsRepeat: boolean;
  wantsFormulae: boolean;
  wantsFunctionGraph: boolean;
  useRealLifeExample: boolean;
};
```

## Scene Output

`SpokenSegment`

```ts
type SpokenSegment = {
  id: string;
  start: string;
  end: string;
  text: string;
  purpose: "intro" | "core_explanation" | "example" | "formula" | "summary";
};
```

`FunctionSpec`

```ts
type FunctionSpec = {
  label: string;
  expression: string;
  shouldShowOnScreen: boolean;
  shouldDrawOnGraph: boolean;
  graphNotes?: string;
};
```

`VisualFrame`

```ts
type VisualFrame = {
  id: string;
  sceneDescription: string;
  timelineStart: string;
  timelineEnd: string;
  formulae: string[];
  functionsToShow: FunctionSpec[];
  functionsToDraw: FunctionSpec[];
  visualizer: "excalidraw" | "manim";
  visualGoal: string;
  visualNotes: string[];
  analogy?: string;
  elementsNeeded?: string[];
};
```

`GeminiSceneOutput`

```ts
type GeminiSceneOutput = {
  answerMode: "simple_explain" | "brief_explain" | "repeat_previous" | "visualize";
  spokenAnswerSegments: SpokenSegment[];
  formulae: string[];
  functions: FunctionSpec[];
  frames: VisualFrame[];
};
```

## Adapter Output

`ExcalidrawFramePlan`

```ts
type ExcalidrawFramePlan = {
  frameId: string;
  title?: string;
  elementsToUse: Array<{
    assetId: string;
    label?: string;
    positionHint: string;
    purpose: string;
  }>;
  textLabels: Array<{
    text: string;
    positionHint: string;
  }>;
  arrows: Array<{
    from: string;
    to: string;
    label?: string;
  }>;
  sequence: Array<{
    step: number;
    action: "place_asset" | "show_text" | "draw_arrow" | "highlight";
    targetIds: string[];
  }>;
};
```

`ManimFramePlan`

```ts
type ManimFramePlan = {
  frameId: string;
  sceneSummary: string;
  objects: Array<{
    type: "text" | "mathtex" | "axes" | "plot" | "shape" | "arrow";
    content?: string;
    expression?: string;
    animation?: string;
    notes?: string;
  }>;
  sequence: Array<{
    step: number;
    action: string;
    targetIds: string[];
    narrationCue?: string;
  }>;
};
```

## Session Memory

`TeachingSessionState`

```ts
type TeachingSessionState = {
  lastQuestion?: string;
  lastIntent?: string;
  lastExplanation?: string;
  lastSpokenSegments?: SpokenSegment[];
  lastFormulae?: string[];
  lastFunctions?: FunctionSpec[];
  lastFrames?: VisualFrame[];
  lastVisualizerOutputs?: FrameVisualizerOutput[];
  lastLessonTimestamps?: Array<{ start: string; end: string }>;
};
```

