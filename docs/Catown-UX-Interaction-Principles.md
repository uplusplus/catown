# Catown UX Interaction Principles

_Last updated: 2026-04-15_

## Purpose

This document captures the current UX conclusions for Catown before deeper wireframing.

It defines the interaction model for the new Catown frontend so product, frontend, and backend work can converge on the same user experience.

Related:

- `docs/Mission-Board-Information-Architecture.md`
- `docs/Mission-Board-Minimum-V2-Contract.md`
- `docs/ADR-023-frontend-react-mission-board-architecture.md`

---

## Product stance

Catown is not a form-heavy dashboard, not a pure chat app, and not an infinitely reshaping canvas.

Catown should behave like an AI operating system for project execution:

- the user gives intent
- the system interprets and structures the work
- the interface reveals only what matters now
- the user is asked for decisions only when needed
- every interaction pushes project state forward

In one sentence:

**Catown is a stable task stage with progressive conversational guidance and structured stateful outcomes.**

---

## Core UX conclusion

The Catown frontend should use this interaction model:

**Stable Mission Board + progressive conversational flow + contextual task cards**

And it should support two complementary interaction channels:

- natural language for expressing intent, revision, interruption, and continuation
- refined UI for presenting state, focusing choices, accelerating action, and building trust

All three structural parts matter.

Without the stable board, the user loses orientation.
Without the conversational flow, complex work becomes rigid and form-heavy.
Without task cards, interaction collapses back into unstructured chat.
Without natural language, the system becomes too rigid.
Without refined UI, the system becomes too vague and too hard to trust.

---

## What Catown should not become

### 1. Not a pile of forms

The UI should not push system modeling work onto the user through large multi-field forms.

A wall of fields is lazy UX.
It forces the user to think in the system's schema before the system has earned that effort.

### 2. Not a pure chat transcript product

A raw chat timeline is too loose for project execution.
It drifts, hides state, and makes it hard to understand what changed or what still needs attention.

### 3. Not an infinitely morphing canvas as the default mode

A totally dynamic canvas sounds powerful but damages orientation, predictability, and trust if used as the primary interaction model.

A freeform canvas may exist later as an exploration mode for relationships, dependencies, or strategy maps, but not as the default operational UI.

---

## Primary interaction principles

### 1. Stable structure first

The user must always be able to answer:

- where am I
- what is the system doing
- what needs my attention
- what should happen next

The overall layout should stay stable enough that the user builds a mental model over time.

### 2. Show state before asking for input

The default experience should present current status, current work, blockers, and pending decisions.

Catown should not open like a chatbot asking the user to type.
It should open like a calm mission control surface.

### 3. Complex work should be guided step by step

When user input is needed, the system should guide the user through the minimum necessary decisions in sequence.

Do not dump a large form.
Do not ask for everything up front.
Ask only for the next missing piece that matters.

### 4. Natural language and UI should complement each other

Catown should support both natural-language interaction and refined graphical interaction as first-class paths.

Natural language is the primary protocol for expressing intent.
UI is the primary medium for making state legible, presenting constrained choices, accelerating action, and building confidence.

They should not compete.
They should reinforce each other.

### 5. Conversation should guide, not replace structure

The interaction can feel conversational, but the outcome must become structured state.

Conversation is the guidance layer.
Structured objects are the execution layer.

### 6. Refined UI is required, not optional

Catown needs UI not just for functionality but for quality.

The interface should feel:

- calm
- precise
- elegant
- trustworthy
- low-friction

Refined UI does not mean ornamental excess.
It means the system presents complex work with clear hierarchy, disciplined spacing, strong visual rhythm, and high-confidence feedback.

### 7. Simple actions should stay simple

Not every interaction deserves a conversational flow.

- simple confirmations should use direct confirm cards
- simple edits should use focused inline controls
- high-frequency micro-actions should complete in one move

Do not force chat theater onto trivial operations.

### 8. AI should absorb complexity

The system should do more interpretation and organization so the user can do less manual configuration.

The user provides intent.
The system proposes structure.
The user corrects only where correction matters.

### 9. The homepage should optimize for progress, not feature discovery

The main surface should emphasize:

- continuing meaningful work
- resolving blockers
- answering pending decisions
- taking the next valuable step

Low-frequency environment actions should remain easy to reach, but they should not dominate the home screen.

---

## Natural language first, UI always available

Every core Catown interaction should be completable through natural language.

Users should be able to initiate, revise, confirm, pause, resume, and redirect work by saying what they want in ordinary language.

But this does not reduce the need for UI.

UI should remain available at every important step as an accelerator and trust surface.

That means:

- users can start with language and finish with cards
- users can click through cards and then correct with language
- users can mix speaking intent and using controls in the same flow
- graphical controls are accelerators, not mandatory gateways

A strong Catown experience lets the user fluidly move between saying and selecting.

---

## Refined UI quality bar

Catown should not settle for merely functional UI.

The interface should feel polished enough that the user experiences the system as competent and composed.

The quality bar should emphasize:

- visual restraint over noise
- strong hierarchy over clutter
- breathing room over density for its own sake
- direct feedback over ambiguity
- clear object boundaries over blended mush
- smooth progression over flashy motion

The target feeling is not decorative futurism.
It is calm high-end operational clarity.

---

## Visual tone

Catown visual design should feel:

- calm
- sharp
- restrained
- futuristic
- premium

This does not mean flashy "AI" aesthetics.
It means a composed interface with strong judgment, clean emphasis, and a sense of system-level quality.

Catown should not look like:

- a chat app with bubbles as the main visual grammar
- a generic enterprise dashboard with equal-weight panels everywhere
- a neon cyberpunk demo overloaded with glow and gradients

The intended effect is:

- calm enough to think clearly
- sharp enough to reveal priorities fast
- restrained enough to feel mature
- futuristic enough to feel like a next-generation work surface
- premium enough to build trust through precision

In one line:

**Catown should feel like a calm, sharp, premium AI operating interface.**

---

## Dark theme direction

Catown should design around a primary dark theme first.

Theme switching may be supported later, but the core visual system should first become excellent in one primary theme instead of becoming diluted by trying to make dark and light equally mature too early.

The dark theme direction should be:

**deep graphite base + cool neutral surfaces + minimal electric accent energy**

This is not a pure-black hacker aesthetic.
It is a high-end operational dark system.

### Dark theme goals

The dark theme should make Catown feel:

- stable
- focused
- high-trust
- system-like
- quietly futuristic

### Dark theme anti-goals

Avoid:

- pure black everywhere
- neon-heavy cyberpunk treatment
- excessive glass effects
- colorful surfaces competing for attention
- weak contrast that turns the interface muddy

---

## Dark palette structure

Catown dark mode should use a tightly controlled palette with five roles.

### 1. Base

The deepest background layer that defines the overall atmosphere.

Direction:

- graphite
- obsidian
- slate-charcoal

It should feel cool and neutral, not purple, not glossy, and not dead black.

### 2. Surface

A clear stack of surface tones should separate:

- app background
- board background
- panels and cards
- raised cards and hover states
- task layers and modal surfaces

Deep UI quality depends on these surface steps being clean and readable.

### 3. Content

Text and icon colors should have clear hierarchy:

- primary content
- secondary content
- tertiary content
- muted or disabled content

The interface should avoid blasting pure white text across the whole product.
Readable contrast should come from controlled hierarchy, not brute force.

### 4. Accent

Catown should use one main accent personality.

Recommended direction:

- cool electric cyan-teal

Why:

- futuristic without becoming cliché
- clean on dark backgrounds
- precise rather than playful
- technical without becoming sterile

The accent should be used sparingly for:

- primary CTA emphasis
- current focus
- selected states
- active input or command states
- key guidance highlights

### 5. Semantic colors

Status colors should remain controlled and purposeful.

- danger: controlled crimson
- warning: muted amber
- success: restrained emerald
- info: accent-tinted cool blue

Semantic colors should communicate state, not decorate empty space.

---

## Color usage principles

### 1. Most of the interface should stay neutral

Roughly 90% of the product should read as neutral dark surfaces, text hierarchy, and restrained borders.

### 2. Color should signal action or meaning

Use color mainly for:

- action priority
- state
- focus
- risk
- confirmation

Do not use color as a filler material.

### 3. Luxury comes from restraint, not spectacle

Catown should earn visual sophistication through spacing, hierarchy, proportion, and tonal control, not through constant visual effects.

### 4. The dark theme should feel like a high-end operations room

The target mood is not a sci-fi gimmick.
It is a composed mission-control environment that feels trustworthy under complexity.

---

## Theme system strategy

Catown should define one baseline dark theme first and express it through replaceable semantic design tokens.

This means:

- the product establishes a single authoritative visual baseline first
- components should consume semantic roles instead of hard-coded color choices
- future themes can remap the same roles without changing interaction logic
- theme switching should extend the design system, not weaken the first theme

The first authoritative theme should be `Catown Dark Baseline`.

---

## Catown Dark Baseline

This is the first recommended baseline palette for the product.

It should serve as the reference implementation for:

- design exploration
- frontend tokens
- component styling
- future theme derivation

### Base and surfaces

```css
--bg-app:        #0B1020;
--bg-board:      #11182A;
--bg-panel:      #151D31;
--bg-elevated:   #1A243A;
--bg-task-layer: #1E2942;
--bg-selected:   #22314D;
```

Usage intent:

- `--bg-app`: deepest application frame
- `--bg-board`: main mission board background
- `--bg-panel`: standard cards and panels
- `--bg-elevated`: hover, raised, or more important surfaces
- `--bg-task-layer`: centered task layer and high-attention overlays
- `--bg-selected`: selected rows, selected cards, or active contextual regions

### Borders and dividers

```css
--border-subtle:  #24324A;
--border-default: #2C3B56;
--border-strong:  #39506F;
```

Borders should support separation and hierarchy without turning the UI into a wireframe.

### Text hierarchy

```css
--text-primary:   #F3F7FF;
--text-secondary: #B6C2D9;
--text-tertiary:  #7F8CA8;
--text-muted:     #5F6C86;
--text-disabled:  #46526A;
```

The system should rely on tiered legibility instead of blasting maximum contrast everywhere.

### Accent system

```css
--accent-primary: #42CFEA;
--accent-hover:   #67DCF2;
--accent-active:  #24B8D6;
--accent-soft:    rgba(66, 207, 234, 0.16);
--accent-ring:    rgba(66, 207, 234, 0.42);
```

Accent intent:

- use for primary CTA emphasis
- use for current focus or active command states
- use for selected or guided interaction moments
- do not flood the whole interface with accent color

### Semantic states

```css
--state-success:      #3FCB8E;
--state-success-soft: rgba(63, 203, 142, 0.14);

--state-warning:      #E7B35A;
--state-warning-soft: rgba(231, 179, 90, 0.14);

--state-danger:       #E06C75;
--state-danger-soft:  rgba(224, 108, 117, 0.14);

--state-info:         #59A8FF;
--state-info-soft:    rgba(89, 168, 255, 0.14);
```

Semantic colors should remain purposeful and localized.
They should not become decorative background paint.

### Recommended application guidance

For the first implementation pass:

- main board uses `--bg-board`
- standard cards use `--bg-panel`
- stronger modules or hover states use `--bg-elevated`
- the guided task layer uses `--bg-task-layer`
- primary actions use `--accent-primary`
- warnings and risk indicators use semantic colors in restrained, local ways

### Baseline palette intent

This palette should make Catown feel:

- calm
- focused
- modern
- premium
- system-like

It should feel AI-native without looking gimmicky.

---

## Typography system

Catown should use a highly restrained type system.

The typography goal is not decorative personality.
It is operational clarity with premium control.

The recommended model is:

- one modern sans-serif family for almost all interface text
- one monospace family for structured system details and technical accents

Catown should avoid mixing multiple display personalities.
Too many font voices weaken system quality.

### Primary sans direction

Recommended stack direction:

```css
font-family: Inter, "PingFang SC", "Noto Sans SC", "Microsoft YaHei UI", sans-serif;
```

Why:

- modern and precise
- strong UI legibility
- good weight range
- stable mixed-language behavior
- premium but not ornamental

### Monospace direction

Recommended stack direction:

```css
font-family: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
```

Use monospace selectively for:

- timestamps
- IDs
- token-like labels
- command fragments
- system metadata
- highly structured micro-information

It should not dominate the interface.
It is a precision accent, not the main voice.

---

## Typography principles

### 1. Typography should direct attention

Catown needs command-grade hierarchy.
The user should immediately understand what deserves focus, what is supporting context, and what is only metadata.

### 2. Headlines should be short and decisive

Headlines should feel like system cues, not marketing copy.

Good direction:

- Current focus
- Pending decisions
- Release blocked
- Continue project

Bad direction:

- conversationally padded or over-explanatory section titles

### 3. Body copy should stay compact

Explanatory copy exists to reduce uncertainty and unblock action.
It should be concise, high-signal, and structured for scanning.

### 4. Metadata should feel tighter and more structured

Status tags, timestamps, counters, and system labels should feel more compact and disciplined than ordinary prose.

### 5. Depth should come from spacing and contrast, not font variety

Catown should feel refined because hierarchy is well controlled, not because the product uses many font styles.

---

## Typography tokens

The initial typography scale should stay compact and controlled.

### Font families

```css
--font-sans: Inter, "PingFang SC", "Noto Sans SC", "Microsoft YaHei UI", sans-serif;
--font-mono: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
```

### Font sizes

```css
--text-display: 32px;
--text-h1:      24px;
--text-h2:      20px;
--text-title:   16px;
--text-body:    14px;
--text-meta:    12px;
```

### Line heights

```css
--leading-display: 1.15;
--leading-h1:      1.2;
--leading-h2:      1.25;
--leading-title:   1.35;
--leading-body:    1.5;
--leading-meta:    1.4;
```

### Font weights

```css
--weight-regular: 400;
--weight-medium:  500;
--weight-semibold:600;
--weight-bold:    700;
```

### Suggested token usage

- `--text-display`: major task-layer titles or primary hero emphasis
- `--text-h1`: main board section anchors or project hero titles
- `--text-h2`: sub-section headers and important panel titles
- `--text-title`: card titles, action labels, emphasized body headings
- `--text-body`: standard descriptive copy and task guidance
- `--text-meta`: timestamps, status microcopy, IDs, and compact labels

### Weight guidance

Use weights conservatively:

- `regular` for most body copy
- `medium` for important inline emphasis and labels
- `semibold` for section titles, card titles, and action hierarchy
- `bold` only for rare key moments where stronger emphasis is truly needed

Thin or ornamental weights should be avoided, especially in dark mode where they become fragile.

---

## Foundational component language

Catown should feel precise and system-grade at the component level.

The foundational visual language should be:

- restrained in shape
- disciplined in spacing
- light in boundary treatment
- quiet in depth effects
- consistent in hierarchy

It should avoid both extremes:

- not soft and toy-like
- not harsh and terminal-like

The goal is cold precision with enough polish to feel premium.

---

## Spacing system

Catown should use an 8pt foundation with 4pt micro-adjustments.

Recommended spacing scale:

```css
--space-1:  4px;
--space-2:  8px;
--space-3: 12px;
--space-4: 16px;
--space-6: 24px;
--space-8: 32px;
--space-10: 40px;
--space-12: 48px;
```

Spacing principles:

- spacing between major modules should be larger than spacing inside modules
- focused task areas should breathe more than passive context areas
- simplicity should come from hierarchy, not from compressing everything together

---

## Radius system

Catown should use medium-small radii with tight discipline.

Recommended scale:

```css
--radius-sm:  8px;
--radius-md: 12px;
--radius-lg: 16px;
--radius-xl: 20px;
```

Usage intent:

- `--radius-sm` for compact controls and small UI elements
- `--radius-md` for standard cards, controls, and compact panels
- `--radius-lg` for larger panels and prominent surfaces
- `--radius-xl` only for major task layers or rare large containers

The product should feel modern but not soft or bubbly.

---

## Border system

In Catown, borders matter more than heavy shadows.

Borders should establish structure, separation, and active state changes with precision.

Recommended widths:

```css
--border-width-thin:   1px;
--border-width-strong: 1.5px;
```

Principles:

- default cards and panels should rely on subtle borders
- hover or active states may strengthen border presence slightly
- focus states can combine border emphasis with accent rings
- borders should support order without turning the UI into outlined boxes everywhere

---

## Shadow system

Shadows should be used sparingly and mainly for elevation cues.

Recommended scale:

```css
--shadow-soft:   0 6px 18px rgba(0, 0, 0, 0.18);
--shadow-raised: 0 10px 28px rgba(0, 0, 0, 0.24);
--shadow-task:   0 20px 48px rgba(0, 0, 0, 0.32);
```

Usage intent:

- `--shadow-soft` for gentle hover or light elevation
- `--shadow-raised` for raised panels that need clear separation
- `--shadow-task` for major overlays such as the centered task layer

Catown should not depend on shadows for its sophistication.
Most depth should come from surface hierarchy, spacing, and border discipline.

---

## Surface hierarchy guidance

Components should respect a clear surface hierarchy:

1. base application frame
2. main mission board plane
3. standard panels and cards
4. elevated or selected surfaces
5. high-attention task layers and overlays

This hierarchy should be more important than one-off component styling flourishes.

---

## Component behavior principles

### 1. Cards are priority containers, not neutral boxes

Card treatment should reflect importance.
Primary cards, supporting cards, and contextual cards should not all feel identical.

### 2. Buttons should feel sharp, not puffy

Buttons should feel confident and compact.
Avoid oversized pill treatments or overly soft consumer-app button styling.

### 3. Inputs should feel like system controls, not chat bubbles

Command triggers, fields, and focused inputs should look like refined operating controls.
They should not visually collapse into IM-style message boxes.

### 4. Status tags should feel disciplined

Tags, pills, and state labels should read as system markers.
They should avoid candy-like colors or playful social-product styling.

---

## Interaction layers

## A. Stable Mission Board

This is the persistent background layer.
It gives the user orientation and project context.

It should answer:

- what project is active
- what stage it is in
- whether progress is healthy or blocked
- what the recommended next action is
- what is waiting on the user
- what recently changed

This layer should feel steady, not noisy.

## B. Progressive conversational flow

This is the foreground interaction layer used when:

- the user issues a new command
- the system needs clarification
- the system needs user confirmation
- the work is complex enough to benefit from guided progression

The flow should:

- accept natural-language intent
- support UI-based selection and confirmation at every key step
- identify the task type
- ask one minimum necessary question at a time
- explain why the question matters when needed
- converge quickly toward an executable draft

This is not generic chatting.
This is guided task progression.

## C. Contextual task cards

Task cards are the execution units inside the flow.

They should be elegant, focused, and fast to parse.

Examples include:

- choice cards
- draft cards
- confirmation cards
- diff cards
- risk cards
- result cards
- summary cards

The conversational layer introduces and explains.
The card layer captures action and confirmation.

Users should be able to complete the same core step by either:

- responding in natural language
- selecting directly on the card
- mixing both in the same task flow

---

## Default home experience

When the user enters Catown, the default page should prioritize present-tense operational awareness.

It should foreground:

### 1. Current primary work

What the system is actively trying to move forward.

### 2. Current stage and health

Where the project is in its lifecycle, and whether it is progressing, blocked, or awaiting input.

### 3. Recommended next action

A clear, opinionated cue for what should happen next.

### 4. Pending user decisions

Anything that needs user review, confirmation, or approval.

### 5. Recent meaningful changes

The most relevant project events, not a noisy raw log.

The homepage should feel like a mission control view, not a feature menu.

---

## Homepage composition

The homepage should not behave like a feature directory, a KPI dashboard, or a chat landing page.

Its first screen should answer three things immediately:

- where the current battle is
- whether the user needs to intervene
- what the next move should be

The recommended composition is:

**one strong center stage with two supporting bands**

That means:

- a narrow left rail for project context switching
- a dominant center stage for the active mission board
- a weak right band for supporting attention items

The visual center of gravity should remain in the middle.
The homepage must not devolve into equal-weight multi-column noise.

### Layout intent

#### Left rail

The left rail is for context switching, not situation explanation.

It should stay narrow and light.

It may include:

- project list
- current project highlight
- light filtering
- low-emphasis create-project entry

It should not become a secondary content area full of logs, metrics, or narrative explanation.

#### Center stage

The center stage is the heart of the homepage.

Its first-screen structure should be vertically organized into three levels:

1. a project hero that defines the current situation
2. an action-focus module that defines the best next move
3. a stage-progress region that explains current progression context

This is where the product earns its operational clarity.

#### Right band

The right side should stay weaker than the center stage.

It should carry supporting attention items such as:

- pending decisions
- key changes
- lightweight situational alerts

It should not compete with the center stage for primary attention.

### Visual priority order

The recommended homepage attention order is:

1. project hero
2. action-focus module
3. pending attention or stage progress
4. recent changes and other supporting context

If the event feed, metadata, or low-frequency features become more visually dominant than the action focus, the homepage has failed.

### Anti-patterns

The first screen should avoid:

- a large persistent chat input
- a heavy chat transcript
- equal-weight statistics cards
- multiple competing CTAs
- a large empty canvas waiting for interaction

The homepage should feel like a composed operations surface, not an information marketplace.

---

## Project hero

The homepage hero is not a banner and not a project profile card.

It is a compressed situation card for the current project.

Its job is to answer:

- what project this is
- what state it is in
- what the system is currently focused on
- how serious the current situation is

### Hero content

The hero should stay disciplined and include only high-value context:

1. project identity
   - project name
   - a single short objective sentence
2. current stage
3. health or risk status
4. current system focus
5. one recent meaningful change at most

The hero should not become a metadata dump.

### Hero structure

A good hero can be thought of as three stacked layers:

#### Identity layer

- project name
- short objective statement

#### Status layer

- current stage
- health or risk state
- optional execution mode when genuinely useful

#### System focus layer

- one sentence describing the system's current focus
- one sentence describing the latest meaningful change

### Hero role in the page

The hero sets the scene.
It should feel steady, high-trust, and situational.

It should not try to become the main action area.
The action-focus module below it should own active progression.

---

## Action-focus module

The recommended next-action area should not be treated as a generic CTA card.

It should behave like a system judgment surface.

Its purpose is to tell the user:

- what matters most right now
- why this is the best next move
- whether the user needs to intervene
- what will happen if they continue

### Core role

This module is the homepage's action engine.
It is where Catown demonstrates that it is not merely showing state, but actively guiding progress.

### Recommended structure

A strong action-focus module contains four parts:

1. a short action label
   - examples: `Continue project`, `Review scope decision`, `Unblock release`
2. one short reason statement
3. one clear primary action
4. one weaker supporting entry when needed

The primary action must be singular.
If the module presents multiple equal-weight main actions, the system is avoiding judgment instead of providing it.

### Action states

The module should support at least these semantic modes:

- progression state
- awaiting-confirmation state
- blocked state
- completion or idle-transition state

Each mode should adjust tone and supporting explanation, but the structure should remain consistent.

### Visual intent

The action-focus module should feel stronger than surrounding support cards, but it should not become a loud marketing banner.

Its authority should come from:

- hierarchy
- wording precision
- surface emphasis
- focused use of action color

Not from oversized color blocks or excessive visual dramatics.

---

## Intervention queue

The homepage should include a dedicated area for decisions that truly require human authority.

This area should not behave like a notification center or a generic todo list.

It should behave like a human intervention queue.

Its message is:

**the system has advanced as far as it safely can, and now it needs your decision**

### What belongs here

Only items that genuinely require user judgment should appear here, such as:

- scope or direction decisions
- release approvals
- high-risk blocker tradeoffs
- missing critical inputs
- option selection where no safe automatic default exists
- unresolved conflicts the system cannot adjudicate responsibly

This queue should stay small and expensive.
If it becomes a dump for ordinary updates, it loses authority.

### What does not belong here

Do not include:

- routine status updates
- low-risk reminders
- actions the system can safely take itself
- minor configuration choices with obvious defaults
- generic event feed items

### Core questions each intervention item should answer

Every item should make four things clear:

1. what the user is being asked to decide
2. why that decision is needed now
3. what the impact of delay or action will be
4. how much effort the decision is likely to require

### Recommended item structure

Each intervention card should include:

1. a plain-language decision title
2. one short explanation of why the user is needed now
3. a compact impact statement
4. a lightweight effort indicator
5. one main review or resolve entry
6. one weaker context entry when needed

The homepage should not expose raw approve or reject controls without context.
The preferred pattern is to send the user into the centered task layer for the actual decision flow.

### Naming direction

This area should use strong system language, not weak inbox language.

Good directions include:

- `Needs your decision`
- `Waiting on you`
- `Your call`

The Chinese direction discussed so far is:

- `待你决断`

### Priority strategy

If multiple intervention items exist, the queue should prioritize them roughly by:

1. blockers that stop current progress
2. decisions that materially affect current-stage quality or direction
3. lower-impact authorizations that still require explicit user approval

The homepage should usually emphasize only the top 1 to 3 intervention items.
Anything larger should compress into a deeper queue or dedicated review surface.

### Visual intent

The intervention queue should feel alert, but not alarmist.

It should be noticeable enough that users know when it is their turn, but not so loud that it overpowers the homepage hero or the main action-focus module.

Its role is not to create anxiety.
Its role is to mark the boundary of system autonomy and hand decision authority back to the user with clarity.

---

## Command entry model

Catown should support command-driven interaction, but the command entry should not feel like a dead text box.

The right model is a lightweight command trigger that opens into a guided interaction flow.

The trigger should accept natural language naturally, but it should also feel like a polished system control rather than a generic chat input.

Possible forms:

- a subtle command bar
- a command trigger in the main board
- keyboard shortcut invocation
- a continue or act-now control attached to the recommended action area

The trigger is not the product.
It is the doorway into guided interaction.

---

## Progressive conversational flow pattern

The standard pattern should be:

### Step 1. Capture intent

The user states a goal in natural language.

Examples:

- create a new project
- continue this project
- summarize blockers
- prepare release

### Step 2. Infer task type

The system determines whether this is primarily:

- creation
- progression
- review
- approval
- analysis
- correction

### Step 3. Ask one minimal question

The system asks only the next question that unblocks forward motion.

This question should usually ask for:

- a choice
- a confirmation
- a priority
- a missing key constraint

### Step 4. Generate a draft or proposed action

The system should synthesize what it understands into a structured draft.

The user should be able to:

- confirm
- revise a specific part
- back up
- restart when necessary

### Step 5. Execute and write state back to the board

The result should update structured domain objects and visible system state.

That may include updates to:

- `Project`
- `StageRun`
- `Decision`
- `Asset`
- `Event`

Without state write-back, the experience collapses into disposable chat.

---

## When to use conversational guidance

### Good fit

Use progressive conversational guidance for:

- project creation
- ambiguous requests
- multi-step planning
- blocker resolution
- high-value decision review
- stage progression where intent needs clarification

### Bad fit

Do not use progressive conversational guidance for:

- basic confirmation
- small single-property edits
- repetitive micro-actions
- obvious one-click actions

The rule is simple:

**Complex tasks should feel guided. Simple tasks should feel immediate.**

---

## Information hierarchy implications

### Priority 0: immediate attention

- decisions awaiting user review
- high-risk blockers
- release or scope confirmations

### Priority 1: active progress

- continue the current project
- execute the recommended next step
- inspect the current stage when it is the main blocker

### Priority 2: supporting context

- recent activity
- assets
- event history
- drill-down details

### Priority 3: environment actions

- create project
- workspace management
- settings-like controls

This means "create project" is important but not homepage-dominant once the user already has active work.
It should be reachable without becoming the main CTA in the normal home state.

---

## Design implications for key disputed points

### Creating a project

Creating a project is important but usually low-frequency.

So:

- it should not dominate the default home screen when active projects already exist
- it can become the main CTA in an empty state
- it should remain easy to reach through command entry, navigation, or contextual triggers

### Chat-like interaction

The desired behavior is not a freeform chat product.
It is a guided, step-by-step, conversational task flow.

This means:

- fewer open-ended text prompts
- more selective system guidance
- more choices and confirmations
- more compact summarization of completed steps
- stronger focus on the current step

### Dynamic UI generation

Dynamic UI generation is useful only when bounded.

The system should dynamically choose the right card or interaction unit for the current step, but it should do so inside a stable board structure.

The system should not continuously reinvent the whole page layout.

---

## Product-quality bar

A strong Catown interaction should feel like this:

- calm at rest
- opinionated about next steps
- lightweight to enter
- guided during ambiguity
- direct during simple operations
- structured after every action

A weak Catown interaction feels like this:

- noisy by default
- chatty without making progress
- form-heavy before context is established
- visually unstable
- unclear about what the user should do next

---

## One-line summary

**Catown should feel like an AI mission control system where users express intent naturally, the system guides them through only the necessary decisions, and every interaction resolves into visible project state.**
