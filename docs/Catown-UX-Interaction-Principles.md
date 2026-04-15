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

Catown must also be designed as a multi-agent operating system, not as the interface of a single assistant.

That means the product should make it legible that:

- multiple agents may be active across the same project
- agents may hand work off to one another
- orchestration quality matters as much as single-response quality
- the user intervenes at the system boundary rather than micromanaging every internal step

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

### 5. AI-era interaction must feel like dialogue, not GUI wizardry

Catown should treat human-AI interaction primarily as dialogue.

That does not mean unstructured chat logs.
It means the system should adapt to human expression instead of forcing people through rigid GUI-era step sequences.

The interaction may still converge step by step, but those steps should feel like a conversation being progressively clarified, not like a Windows-style wizard being mechanically advanced.

This means:

- users can interrupt and redirect the flow
- users can revise earlier assumptions without feeling trapped in a funnel
- the system should skip unnecessary questions when intent is already clear
- the UI should compress the current decision, not force the user into machine-shaped order

### 6. Conversation should guide, not replace structure

The interaction can feel conversational, but the outcome must become structured state.

Conversation is the guidance layer.
Structured objects are the execution layer.

### 7. Refined UI is required, not optional

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

### 9. AI should absorb complexity

The system should do more interpretation and organization so the user can do less manual configuration.

The user provides intent.
The system proposes structure.
The user corrects only where correction matters.

### 10. The homepage should optimize for progress, not feature discovery

The main surface should emphasize:

- continuing meaningful work
- resolving blockers
- answering pending decisions
- taking the next valuable step

Low-frequency environment actions should remain easy to reach, but they should not dominate the home screen.

### 11. Multi-agent orchestration should remain visible

Catown should not collapse the product experience into the illusion of one assistant continuously speaking.

The interface should preserve legibility around:

- agent activity
- handoffs between agents
- autonomy health
- orchestration bottlenecks
- human intervention boundaries

The user should perceive a coordinated system, not a single chat persona pretending to do everything alone.

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

## Dialogue-first progression

Catown should avoid the feel of a classic wizard.

Even when the system converges through stages, the user should feel that they are in a live conversation with a system that understands, adapts, and reorganizes around their intent.

That means:

- the user can jump ahead when they already know what they want
- the system can skip no-longer-necessary questions
- the user can revise a prior decision in natural language
- the system should prefer local correction over forcing linear backtracking
- cards should represent the current decision node, not a static page in a rigid funnel

In short:

**progressive interaction should feel like dialogue being compressed into a current decision, not like a GUI wizard demanding the next click.**

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

### Cockpit-first clarification

After project kickoff, Catown should assume that the user spends most of their time supervising autonomous execution rather than manually operating every step.

That means the default homepage should feel like an auto-mode mission cockpit.

The homepage should primarily help the user monitor:

- what the system is currently doing
- which agents are active and what they are doing
- whether execution is healthy or abnormal
- whether progress is smooth or blocked
- whether human authority is currently needed

This shifts the default emphasis from "what would you like to do" toward "what is the autonomous system doing and when should you step in".

Because Catown is multi-agent by design, this cockpit view should make orchestration visible rather than flattening everything into one assistant voice.

### Default monitoring priorities

The homepage should therefore balance around these monitoring priorities:

1. system situation
2. agent and execution activity
3. intervention needs
4. progression health
5. filtered recent changes

The interface should feel closer to a mission-control cockpit than to an app launcher or chat home.

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

### Agent and autonomy status

The homepage should include a lightweight execution-monitoring surface for autonomy status.

This can appear either as:

- an `AutonomyStatusBand` near the top of the center stage
- or a compact `AgentActivityStrip` embedded near the hero/action-focus relationship

Its purpose is to show:

- whether the system is actively running in auto mode
- which major agents are currently active
- whether any agent is stalled, waiting, or abnormal
- the latest meaningful execution pulse

This area should not become a raw log.
It should act like a cockpit pulse indicator for autonomous activity.

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

## Stage spine

The homepage should include a stage-progress region that behaves like a progression spine, not a generic timeline.

Its purpose is to help the user quickly understand:

- where the project currently sits in its progression structure
- what major gates have already been crossed
- what the current phase is blocked on or waiting for
- what major gates are still ahead

This area should express project growth structure, not historical exhaust.

### What the stage spine is

The stage spine is a structural progress view.
It is not:

- a raw event stream
- a PM-style gantt or milestone chart
- a checklist of micro-actions
- a full process diagram

It should feel like the backbone of the project's advancement.

### What it should show

The stage spine should focus on high-value structural signals:

- major stage nodes
- the current active stage
- completed stages
- blocked or waiting states
- the next major gate

### What it should avoid

Do not overload it with:

- small operational steps
- long narrative explanations
- timestamp-heavy history
- many inline actions on each node
- full event details

### Recommended state model

A simple state vocabulary is preferred.
For the homepage view, stage nodes should usually map to one of:

- `done`
- `active`
- `blocked`
- `awaiting_decision`
- `upcoming`

This keeps the structure understandable at a glance.

### Current-stage emphasis

The current stage must be visually dominant within the spine.

Users should immediately see where the project currently sits.

The current node may carry slightly richer information than other nodes, such as:

- stage name
- current state
- a short current objective
- a blocker or decision pointer when relevant
- a context or inspect entry

Completed and future nodes should remain legible but clearly subordinate.

### Homepage evolution into Navigation Core

For the more advanced cockpit-oriented homepage, the stage spine should no longer appear as a separate generic module beneath the hero.

Instead, it should evolve into the route-visualization layer inside the `Navigation Core`.

That means the homepage center should increasingly be treated as:

- mission target
- flight state
- current position
- next gate
- route visualization

rather than hero plus separate stage module.

### Route visualization role

Inside `Navigation Core`, the route visualization should serve three jobs at once:

1. show the overall route through major stages or gates
2. show the current position and route health
3. act as a drill-down entry into stage detail

This route view is not a decorative progress bar.
It is the system's main navigation surface for project progression.

### Drill-down behavior

Each major route node should support stage-level inspection.

Typical drill-down expectations:

- current node -> current stage detail and blockers
- completed node -> what was produced or cleared in that stage
- upcoming node -> what conditions or decisions are needed to reach it

This makes the route a true navigation system rather than a passive display.

### Homepage placement implication

In the cockpit-first homepage, the route visualization belongs inside the center `Navigation Core`, not as a disconnected lower module.

### Orientation guidance

For the homepage default state, a vertical or near-vertical spine is preferred over a broad horizontal roadmap.

Why:

- it supports stronger current-stage emphasis
- it fits the homepage reading rhythm more naturally
- it adapts better to responsive layouts
- it avoids turning into a wide project-plan banner

### Relationship to other modules

The stage spine should stay distinct from the homepage hero and the event feed.

- the hero explains the overall project situation
- the stage spine explains where the project sits in its progression structure
- the event feed explains what changed over time

The spine provides structural position, not chronological narration.

### Visual intent

The spine should feel:

- structured
- calm
- legible
- current-stage centered
- resistant to project-management-tool clichés

It should help the user feel that the project is growing along a clear backbone, not dissolving into a stream of disconnected activity.

---

## Homepage wireframe structure

The homepage should now be concrete enough to describe as a first-pass wireframe.

This wireframe is not the final visual design.
It is the structural blueprint for the default desktop mission-board state.

### First-screen desktop structure

```text
+------------------+------------------------------------------------------+-----------------------+
| Left rail        | Center stage                                         | Right band            |
|                  |                                                      |                       |
| Projects         | [ Project Hero ]                                     | [ Needs your decision ]
| - Project A      |                                                      | - item 1              |
| - Project B      | [ Action-focus module ]                              | - item 2              |
| - Project C      |                                                      | - item 3 / view all   |
|                  | [ Stage spine ]                                      |                       |
| + New (light)    |                                                      | [ Key changes ]       |
+------------------+------------------------------------------------------+-----------------------+
```

### Reading order

The intended reading sequence is:

1. identify the active project in the hero
2. understand the best next move in the action-focus module
3. inspect where the project sits in the stage spine
4. check whether user intervention is waiting in the right band
5. scan key changes only after the operational picture is clear

### Width guidance

For desktop, the visual balance should roughly feel like:

- left rail: 14% to 18%
- center stage: 62% to 72%
- right band: 14% to 20%

This should remain center-dominant.
The center stage must clearly own the page.

---

## Homepage module stack

### Left rail

```text
[ Project search / light filter ]
[ Project list ]
[ Low-emphasis create project entry ]
```

Rules:

- no heavy analytics
- no large narrative blocks
- no event-feed duplication
- keep it navigational and quiet

### Center stage

```text
[ Project Hero ]
  - project name
  - short objective
  - stage
  - health/risk
  - current system focus
  - latest meaningful change

[ Action-focus module ]
  - short action label
  - reason statement
  - primary action
  - optional secondary context entry

[ Stage spine ]
  - done stages
  - current active stage (expanded)
  - upcoming stages
```

Rules:

- the hero sets context, not action overload
- the action-focus module owns the main forward move
- the stage spine provides structural progression, not narrative logs

### Right band

```text
[ Needs your decision ]
  - top 1 to 3 intervention items
  - review / resolve entry

[ Key changes ]
  - small list of filtered meaningful updates
```

Rules:

- intervention items outrank passive updates
- key changes remain compact
- the right side stays visually weaker than the center stage

---

## Homepage visual hierarchy rules

### 1. The hero is the visual anchor

It should be the first stable orientation point.

### 2. The action-focus module is the strongest operational cue

It should be the most decisive module on the page, even if it is not the visually largest block.

### 3. The intervention queue should feel important but bounded

It should communicate that the user's authority is required without turning the page into an alarm surface.

### 4. The stage spine should feel like structure, not spectacle

It should visually support understanding, not compete with the hero.

### 5. Key changes should be compressed and filtered

They are supporting pulse, not the main story.

---

## Homepage mobile adaptation

On smaller widths, the homepage should stack in this order:

```text
[ Project switcher ]
[ Project Hero ]
[ Action-focus module ]
[ Needs your decision ]
[ Stage spine ]
[ Key changes ]
```

The mobile version should preserve the same priority model even when the columns collapse.

The action-focus module must still remain near the top.
The user should not have to scroll through passive context before reaching the main move.

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

## Centered task-layer wireframe

The centered task layer is the default active-state work surface for complex interaction.

It should feel like a task panel rising out of the mission board, not like a detached page or a small modal.

### Outer-frame intent

The task layer should be:

- centered
- large enough to feel like a real work surface
- visually elevated above the board
- still clearly connected to the board behind it

Recommended desktop direction:

- width: roughly 64% to 72% of the main content zone
- max width: approximately 960px to 1120px
- height: roughly 72% to 84% of viewport height

The background mission board should remain visible in a softened state.

### Outer-frame wireframe

```text
                   ┌──────────────────────────────────────────────┐
                   │ Centered Task Layer                         │
                   │                                              │
                   │ Header                                       │
                   │ Main Step Area                               │
                   │ History Summary                              │
                   │ Bottom Action Bar                            │
                   │                                              │
                   └──────────────────────────────────────────────┘

      Background mission board remains visible, dimmed, and de-emphasized
```

### Internal proportion guidance

The internal structure should follow a strong emphasis model:

- header: 10% to 12%
- main step area: 58% to 68%
- history summary: 10% to 14%
- bottom action bar: 12% to 14%

This should not feel evenly divided.
The main step area must clearly dominate.

---

## Task-layer header wireframe

The header is a task context bar, not a page header.

Its role is to keep the user anchored in:

- what task is happening
- what project or stage it belongs to
- what the current convergence status is
- how to pause or exit

### Header wireframe

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Create project                                                            │
│ AI Recruitment Site / Initial planning     Goal understood, choosing path │
│                                                              Later   Close│
└────────────────────────────────────────────────────────────────────────────┘
```

Header rules:

- the task title is the strongest element
- project or stage context stays secondary
- progress language should feel like live convergence, not wizard numbering
- pause or close actions must exist but stay visually quiet

---

## Main-step area wireframe

The main-step area is the center of the task layer.

It should function as a current-step focus area, not a content dump.

### Main-step template

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ I’ll first help narrow the starting direction.                            │
│ Confirm this step and I’ll generate the first project draft.              │
│                                                                            │
│   ┌────────────────────────────────────────────────────────────────────┐   │
│   │ Primary Card                                                      │   │
│   │                                                                    │   │
│   │  [ option / draft / comparison / risk / confirmation ]            │   │
│   │                                                                    │   │
│   └────────────────────────────────────────────────────────────────────┘   │
│                                                                            │
│ Optional lightweight supporting note                                       │
└────────────────────────────────────────────────────────────────────────────┘
```

Rules:

- one short guidance block only
- one primary card only
- optional support note only when genuinely needed
- the user's eye should land on the primary card immediately after reading the guidance

The main-step area should support local scrolling when complex cards need more space, while header, summary, and bottom action bar remain stable.

---

## History-summary wireframe

The history-summary area exists to provide convergence feel without turning the task layer into a transcript.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Goal defined        Prototype-first selected        Draft almost ready     │
└────────────────────────────────────────────────────────────────────────────┘
```

Rules:

- compressed, short summaries only
- default collapsed or lightweight presentation
- enough to reassure the user that the flow is progressing
- never large enough to become a scrolling conversation log

---

## Bottom action-bar wireframe

The bottom bar is the task rhythm controller.

It should remain fixed and stable.

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ Back                                              Later     Create project │
└────────────────────────────────────────────────────────────────────────────┘
```

Rules:

- one primary action only
- primary action should describe forward movement, not generic submission
- secondary action is usually back only when a true prior decision surface exists
- weak actions such as later should remain visibly subordinate
- the bar should not turn into a toolbar

### Action-bar principle

"Back" is not mandatory everywhere.
It should appear when returning to a previous decision surface is more natural than making a local correction.

In many cases, local correction is the better dialogue-first pattern.

---

## Full task-layer wireframe

```text
                   ┌──────────────────────────────────────────────────────┐
                   │ Create project                                      │
                   │ AI Recruitment Site / Initial planning   Narrowing  │
                   │                                               ×      │
                   ├──────────────────────────────────────────────────────┤
                   │ I’ll first help narrow the starting direction.      │
                   │ Confirm this step and I’ll generate the first draft.│
                   │                                                      │
                   │   ┌──────────────────────────────────────────────┐   │
                   │   │ Direction Choice Card                        │   │
                   │   │ ○ Product planning first                     │   │
                   │   │ ● Page prototype first                       │   │
                   │   │ ○ Technical plan first                       │   │
                   │   └──────────────────────────────────────────────┘   │
                   │                                                      │
                   │ Optional support note                               │
                   ├──────────────────────────────────────────────────────┤
                   │ Goal defined     Path selected     Draft next        │
                   ├──────────────────────────────────────────────────────┤
                   │ Back                                Later   Continue │
                   └──────────────────────────────────────────────────────┘
```

### What this wireframe establishes

This structure establishes that the task layer should feel:

- dialogue-first rather than wizard-like
- focused on one current decision at a time
- anchored in task context
- stable in its exits and controls
- visibly connected to the mission board behind it

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

The system should not ask questions mechanically just because a previous flow map expected them.
If the user's intent already answers a step, that step should collapse.

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

## Standard task-layer example: create project

Project creation should be the first canonical example of the centered task-layer model.

It demonstrates how Catown should turn a vague user idea into a structured project object without forcing the user through a traditional form.

### Entry paths

A user may enter the flow through either:

- natural language, such as "create a new project" or "start an AI recruitment site project"
- a UI trigger, such as a command surface, low-emphasis create entry, or empty-state CTA

In either case, the centered task layer should open and take over the progression rhythm.

### Step 0. Open the task layer

The task layer appears in centered mode.
It should establish:

- task title
- current context
- lightweight progress state
- clear exit or pause controls

### Step 1. Capture the initial idea

Goal:

- let the user express the project in ordinary language
- avoid making the user think in schema terms too early

Guidance direction:

- invite the user to describe the project goal in one sentence
- explain that Catown will turn it into an executable draft

Primary interaction unit:

- a command-style natural-language input card

This is an exception where open-ended input is the right first move, because the system is still collecting raw intent rather than asking for structured confirmation.

### Step 2. Confirm the first directional framing

After the user's first description, the system should make an initial interpretation.

Example system move:

- identify the likely project type
- reduce ambiguity
- propose the next dimension that actually matters

Guidance direction:

- explain the initial understanding in one short sentence
- ask the user what kind of output should be prioritized first

Primary interaction unit:

- a direction-choice card

Example options:

- product planning first
- page prototype first
- technical implementation plan first

This is the first strong moment of structured convergence.

### Step 3. Determine starting readiness

Once the user chooses a direction, the system should determine how far along the user already is.

Guidance direction:

- explain that the system needs to know the project's starting readiness to place it correctly

Primary interaction unit:

- a readiness-choice card

Example options:

- just an idea, not yet detailed
- direction is known, ready to break down pages or work
- already in progress, Catown should take over continuation

This lets the system infer a reasonable initial stage without requiring the user to learn Catown's internal lifecycle model.

### Step 4. Generate the initial draft

At this point, the system should stop asking and start synthesizing.

Guidance direction:

- explain that an initial project draft is now ready
- frame the next step as confirmation or correction, not more data entry

Primary interaction unit:

- a project draft card

The draft card should show only the highest-value structured fields, such as:

- project name
- project objective
- initial stage
- first output direction
- recommended next action

Primary actions may include:

- confirm creation
- revise name
- revise direction
- redefine objective

This is where the user should feel that the system is doing the modeling work.

### Step 5. Support local corrections

If the user changes one part of the draft, the system should enter local correction mode rather than resetting the whole flow.

Examples:

- rename the project
- switch from prototype-first to planning-first
- tighten or reframe the objective

Primary interaction unit:

- one focused correction card at a time

The system should preserve already-good decisions while adjusting only the targeted part.

This local-edit behavior is a major quality marker for the task-layer design.

### Step 6. Confirm and write back

After confirmation, the system should:

- create the project object
- assign its initial stage or state
- establish the next recommended action
- return the result to the homepage mission board

The UI should not stop at a plain success message.
It should visibly update the board so the user can see:

- the new project in the hero context
- the updated action-focus module
- the initial stage reflected in the stage spine

### What this flow proves

A strong create-project flow proves that Catown can:

- accept vague intent in natural language
- progressively reduce ambiguity
- use cards for focused convergence
- generate structure on the user's behalf
- support local correction without collapse
- write the outcome back into visible system state

In short, the user provides the idea and Catown builds the project skeleton.

---

## Standard task-layer example: continue project

Continuing a project should be the second canonical task-layer flow.

It expresses Catown's core operating value: not just creating project shells, but actively helping existing work move forward.

This flow is different from project creation.
It usually starts from known system state, so the system should judge first and ask second.

### Entry paths

A user may enter the flow through:

- the homepage action-focus module
- a light continue entry in hero context
- natural language such as "continue this project" or "help me move this forward"

### Step 0. Open the task layer

The task layer opens in centered mode and establishes:

- task title
- project context
- current stage context
- exit or pause controls

### Step 1. System situation scan

Before asking the user for anything, Catown should assess the current project situation.

It should check:

- current stage
- blockers
- pending decisions
- recommended next action
- recent meaningful changes
- whether there is a safe direct continuation path

Guidance direction:

- briefly tell the user that Catown is reviewing the best continuation path
- avoid asking questions before this judgment is formed

This is where the system earns trust by demonstrating situational awareness.

### Step 2. Present the continuation judgment

After the scan, the system should present a clear judgment about how continuation should proceed.

Common judgment types include:

1. direct continuation is safe
2. a blocker must be resolved first
3. several valid paths exist and the user must choose one
4. continuation should pause until a risk or decision is reviewed

Guidance direction:

- explain the recommended path in one short decisive sentence
- explain why this is the right continuation move now

This is the key AI-OS moment of the flow.
The system is not just exposing controls, it is forming a continuation judgment.

### Step 3. Route to the correct primary card

Based on the judgment, the flow should route to one primary interaction card.

#### Case A. Direct continuation

Primary unit:

- continuation confirmation card

This card may show:

- current stage
- what will happen next
- expected output
- low-risk indication when relevant

#### Case B. Resolve blocker first

Primary unit:

- blocker analysis card

This card may show:

- blocker summary
- why it is blocking progress
- what delay affects
- what the system recommends resolving first

#### Case C. Choose between valid paths

Primary unit:

- path selection card

This card may show:

- option A
- option B
- option C
- cost, speed, or risk differences

#### Case D. Review risk before continuing

Primary unit:

- risk review or state-review card

This card may show:

- why direct continuation is not recommended
- what risk is currently dominant
- what should be reviewed before action proceeds

The important rule is that "continue project" is not one card shape.
It is a routing flow that chooses the right card based on system judgment.

### Step 4. Enter local subflow when needed

If the judgment leads to blocker resolution, path choice, or risk review, the flow should continue into a shorter local subflow.

This means continue-project is a parent flow that can hand off into a more specific guided interaction.

That handoff is a feature, not a flaw.
It reflects real project progression logic.

### Step 5. Execute and write back

After the user confirms continuation, resolves the blocker, or chooses the path, the system should write the result back into visible board state.

That should update, when relevant:

- hero focus
- action-focus module
- intervention queue
- stage spine
- key changes

The user should be able to feel that the project state actually moved.

### What this flow proves

A strong continue-project flow proves that Catown can:

- inspect existing project state before asking for input
- form a judgment about the best continuation path
- explain when continuation is safe versus premature
- route the user into the right local decision flow
- update visible system state after the decision is made

In short, continuing a project is not a blind "go" action.
It is a short, high-quality progression decision session.

---

## Standard task-layer example: handle decision or approval

Decision and approval handling should be the third canonical task-layer flow.

It defines how Catown behaves when the system reaches a boundary where human authority is required.

This is not a lightweight approve or reject click.
It is a contextual handoff of decision power.

### Entry paths

A user may enter this flow through:

- the homepage intervention queue
- the homepage action-focus module when the most important next step is a decision
- contextual review entry points elsewhere in the board

### Step 0. Open the task layer

The centered task layer should establish:

- decision title
- related project or stage context
- high-level decision type
- exit or pause controls

### Step 1. Explain why the user is needed

Before presenting options, the system should clarify why this decision cannot be made automatically.

Guidance direction:

- explain what the decision is
- explain why Catown cannot safely choose on the user's behalf
- explain what progress is blocked or gated until the choice is made

This is the moment where the user should feel that their authority is being respected rather than their time being wasted.

### Step 2. Present the decision card

The decision itself should be rendered as a focused primary card.

Common card types include:

#### A. Path or direction choice card

Use for:

- scope direction
- execution path choice
- release strategy choice

The card may show:

- option name
- short conclusion
- expected benefit
- expected cost
- risk or tradeoff

#### B. Approval card

Use for:

- release approval
- publish approval
- final pack approval

The card may show:

- what is ready
- what conditions are satisfied
- what risks remain
- what the system will do after approval

#### C. Risk tradeoff card

Use for:

- blocker handling choices
- delay versus scope reduction decisions
- conservative versus aggressive execution decisions

The card may show:

- the core conflict
- option impacts
- recommended direction
- why Catown recommends but does not automatically execute it

### Step 3. Give a recommendation without taking authority

Catown should not pretend to be neutral.
It should make a recommendation when it has a meaningful judgment.

But it should not silently take the choice away from the user.

Guidance direction:

- state which option is recommended
- explain the reason in one short sentence
- preserve the user's authority over the final decision

This balance is essential.
The system should feel opinionated but not overreaching.

### Step 4. Confirm with context

The task layer should provide a clear action path such as:

- choose this path
- approve and continue
- use recommended option
- review details first

The user should be able to inspect more context when needed, but the primary flow should still feel compact.

The task layer should not expose raw low-context approval buttons on the homepage itself.
The real decision belongs inside this contextual review surface.

### Step 5. Write the decision back into project state

After confirmation, the system should explain what it will do next and visibly update the mission board.

Relevant updates may include:

- intervention queue shrinking or clearing
- action-focus module changing to the next move
- stage spine advancing or unblocking
- hero focus updating
- key changes reflecting the decision outcome

A flat "approved" or "done" message is not enough.
The user should feel the baton pass back to the system.

### What this flow proves

A strong decision or approval flow proves that Catown can:

- recognize the boundary of system autonomy
- explain why user authority is required
- compress a decision into a clear and contextual card
- provide a recommendation without stealing control
- resume system execution after the user decides

In short, Catown prepares the decision, and the user owns the final branch.

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

## Interaction system summary

At this point, the Catown interaction model can be summarized as one coherent system.

### 1. Resting state: homepage mission board

The default homepage is the calm operational surface.

Its job is to show:

- the current project situation through the hero
- the best next move through the action-focus module
- human-required decisions through the intervention queue
- progression structure through the stage spine
- filtered pulse through key changes

This is the system-at-rest view.
It should orient the user before asking anything from them.

### 2. Active state: centered task layer

When the user issues a command, opens a key action, or enters a decision boundary, Catown shifts into a centered task layer.

That layer should follow the four-part structure already defined:

- task header
- current-step main area
- compressed history summary
- fixed bottom action bar

This is the system-in-collaboration view.
It should feel like one focused task is being advanced, not like a generic chat transcript.

### 3. Language and UI work together

The interaction engine is dual-channel:

- natural language captures intent, revision, interruption, and correction
- UI cards compress choices, expose structure, and accelerate confirmation

The target behavior is fluid mixing, not forced mode switching.
Users should be able to start with language, continue with cards, and correct with language again.

### 4. Canonical task flows

The first three canonical flows now define the core product behavior:

- create project
- continue project
- handle decision or approval

Together they cover:

- turning vague intent into structured work
- moving active work forward through system judgment
- returning authority to the user at the boundary of automation

### 5. Control model

The control philosophy should remain consistent across the product:

- the system should do as much interpretation and preparation as it safely can
- the user should only be pulled in for the next necessary decision
- once the user decides, Catown should resume forward motion and visibly update state

This is the core loop:

**show state -> focus the next move -> gather only the needed decision -> update visible project state**

### 6. Product identity implication

This unified interaction model is what separates Catown from:

- dashboard software that only displays status
- chat products that never solidify state
- workflow tools that dump structure work onto the user

Catown should feel like a project-operating interface that can both think and stage work clearly.

---

## Frontend implementation breakdown

The design is now specific enough to translate into an implementation-oriented frontend plan.

This section turns the interaction model into concrete frontend responsibilities.

### Semantic rendering architecture

Because the system brain is an LLM and most raw output begins as text, Catown should include a semantic rendering layer rather than relying on raw text dumps or freeform model-generated HTML.

The correct model is:

- LLM output expresses UI intent
- a rendering adapter translates that intent into stable semantic card payloads
- the frontend renders those payloads through controlled design-system components

Catown should not treat model-generated HTML as the primary rendering strategy.

That would create instability in:

- hierarchy
- design consistency
- interaction safety
- testability
- component reuse

Instead, the rendering architecture should be:

### Layer 1. LLM intent output

The model expresses:

- guidance copy
- recommendation copy
- rationale
- options
- action labels
- decision structure
- semantic card type

### Layer 2. Semantic UI adapter

A translation layer normalizes model output into frontend-safe semantic objects such as:

- `choice_card`
- `draft_card`
- `approval_card`
- `risk_card`
- `comparison_card`
- `continuation_card`
- `blocker_card`

### Layer 3. Controlled component rendering

The frontend maps semantic card objects into stable components from the Catown design system.

This is the layer that guarantees:

- consistent visual language
- safe interaction behavior
- predictable state wiring
- shared token usage
- component-level testing

This rendering strategy is essential to keep Catown dynamic without turning it into arbitrary HTML generation.

Because Catown is multi-agent, this same semantic layer should also support orchestration-facing structures such as:

- autonomy status summaries
- agent activity summaries
- handoff and blockage indicators
- intervention-boundary signals

---

### 1. Top-level page structure

The default application shell should be built from two primary UI states:

#### A. Mission-board state

The resting state of the application.

Primary regions:

- `ProjectRail`
- `MissionBoardCenter`
- `MissionBoardRightBand`
- optional `CommandTrigger`

#### B. Centered-task-layer state

The active collaboration state displayed above the mission board.

Primary regions:

- `TaskLayerShell`
- `TaskHeader`
- `TaskStepArea`
- `TaskHistorySummary`
- `TaskActionBar`

The task layer should be mounted as a high-level overlay state, not as a separate route-first page by default.

---

### 2. Homepage component map

A practical component breakdown for the default homepage is:

```text
AppShell
  ProjectRail
    ProjectSearch
    ProjectList
    CreateProjectEntry

  MissionBoardCenter
    ProjectHero
    AutonomyStatusBand / AgentActivityStrip
    ActionFocusModule
    StageSpine

  MissionBoardRightBand
    InterventionQueue
    KeyChangesList

  CommandTrigger
  TaskLayerShell (conditional)
```

Recommended responsibilities:

- `ProjectRail`: context switching only
- `ProjectHero`: project situation compression
- `AutonomyStatusBand` or `AgentActivityStrip`: autonomous execution pulse and agent status
- `ActionFocusModule`: main forward move
- `StageSpine`: progression structure
- `InterventionQueue`: human-authority queue
- `KeyChangesList`: filtered pulse
- `CommandTrigger`: opens intent entry into the task layer
- `TaskLayerShell`: hosts all complex guided interaction

---

### 3. Task-layer component map

The centered task layer should not become one giant monolith.

A practical breakdown is:

```text
TaskLayerShell
  TaskBackdrop
  TaskPanel
    TaskHeader
    TaskStepArea
      TaskGuidance
      TaskPrimaryCardHost
      TaskSupportNote
    TaskHistorySummary
    TaskActionBar
```

Then inside `TaskPrimaryCardHost`, the frontend should swap focused card types such as:

- `IntentInputCard`
- `ChoiceCard`
- `DraftCard`
- `ComparisonCard`
- `RiskCard`
- `ApprovalCard`
- `BlockerCard`
- `ContinuationCard`

This keeps the task-layer shell stable while allowing card-level specialization.

---

### 4. Frontend state model

The frontend state should separate board state from task-layer state.

#### A. Board state

This covers the resting mission-board view.

Suggested state buckets:

- selected project id
- project overview data
- autonomy or agent activity data
- action-focus data
- intervention queue data
- stage spine data
- key changes data

#### B. Task-layer state

This covers active guided interaction.

Suggested state buckets:

- task layer open or closed
- task type
- task context object
- current step id
- current primary card type
- current draft or decision payload
- summarized completed steps
- available actions for the current step

#### C. Async state

Keep async state explicit rather than burying it in each component.

Suggested buckets:

- board loading state
- task action pending state
- refresh state after task completion
- optimistic write-back state where appropriate
- recoverable error state

---

### 5. Suggested task-layer state shape

A useful mental model is:

```ts
interface TaskLayerState {
  open: boolean
  taskType: 'create_project' | 'continue_project' | 'decision_review' | null
  context: {
    projectId?: string
    stageRunId?: string
    decisionId?: string
  }
  currentStep: {
    id: string
    cardType: string
    guidance: string[]
    supportNote?: string
  } | null
  draft: Record<string, unknown>
  summaryChips: string[]
  actions: {
    primary?: { label: string; action: string }
    secondary?: { label: string; action: string }
    weak?: { label: string; action: string }
  }
}
```

The exact shape can evolve, but the conceptual split should remain.

---

### 6. Interaction-flow to component mapping

#### Create project

Typical mapping:

- initial expression -> `IntentInputCard`
- direction selection -> `ChoiceCard`
- readiness selection -> `ChoiceCard`
- draft confirmation -> `DraftCard`
- local correction -> focused card swap

#### Continue project

Typical mapping:

- system scan -> `TaskGuidance` + loading or transition state
- continuation judgment -> `ContinuationCard` or `BlockerCard` or `ChoiceCard`
- local handoff -> route to a focused sub-card
- completion -> board refresh and task-layer close or success state

#### Decision or approval

Typical mapping:

- why user is needed -> `TaskGuidance`
- decision context -> `ApprovalCard` or `ComparisonCard` or `RiskCard`
- recommendation -> card-level recommendation treatment
- confirmation -> `TaskActionBar` primary action
- post-confirmation -> board write-back and queue update

---

### 7. Data-contract implications

The frontend should avoid raw backend leakage into display components.

Use a frontend adapter layer to normalize backend data into view-friendly objects for:

- hero state
- autonomy status
- agent activity summaries
- action-focus state
- intervention items
- stage spine nodes
- task-layer card payloads

This avoids coupling UI semantics directly to backend field noise.

For model-driven task rendering specifically, prefer a structured semantic payload over raw HTML.

A useful target shape is closer to:

```json
{
  "type": "risk_card",
  "title": "Release strategy decision",
  "guidance": ["Current release requires a scope and stability tradeoff."],
  "recommendation": "conservative",
  "options": [
    {"id": "fast", "label": "Ship fast", "risk": "high"},
    {"id": "conservative", "label": "Ship conservatively", "risk": "medium"}
  ],
  "actions": {
    "primary": {"label": "Use recommended option", "action": "choose_recommended"}
  }
}
```

Then the frontend renders this through a controlled `RiskCard` component.

---

### 8. Design-token application points

The token system already defined in this document should be applied through semantic usage, not one-off styling decisions.

Suggested token application map:

- mission-board background -> `--bg-board`
- standard cards -> `--bg-panel`
- raised modules -> `--bg-elevated`
- task layer -> `--bg-task-layer`
- primary text -> `--text-primary`
- secondary text -> `--text-secondary`
- main action emphasis -> `--accent-primary`
- focused selection ring -> `--accent-ring`
- warnings and blockers -> semantic state tokens only

The frontend should centralize these tokens in one theme source rather than scattering values across components.

---

### 9. Implementation order

A sensible frontend implementation sequence is:

1. stabilize the mission-board shell
2. implement `ProjectHero`, `ActionFocusModule`, `InterventionQueue`, and `StageSpine`
3. build `TaskLayerShell` with static wireframe structure
4. implement reusable task cards
5. wire create-project flow end to end
6. wire continue-project flow end to end
7. wire decision or approval flow end to end
8. tighten board write-back and refresh behavior
9. refine empty, loading, and error states

This order follows product importance instead of component novelty.

---

### 10. Implementation risk to avoid

The biggest frontend risk is slipping back into either of these two failures:

#### Failure A. Chat-first collapse

Symptoms:

- task layer becomes a transcript stack
- current-step focus weakens
- cards become decoration instead of the decision surface

#### Failure B. Wizard-first collapse

Symptoms:

- rigid previous/next flow dominates
- local correction becomes hard
- the interface feels like a classic funnel instead of AI dialogue

The implementation should preserve the middle path:

**dialogue-first, card-centered, stateful, and visibly connected to the mission board.**

---

## One-line summary

**Catown should feel like an AI mission control system where users express intent naturally, the system guides them through only the necessary decisions, and every interaction resolves into visible project state.**
