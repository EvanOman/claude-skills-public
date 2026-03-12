---
name: design-polish
description: Polish frontend design with distinctive aesthetics - asks for style vibe first. Use when the user wants to improve, polish, or elevate the visual design of a web project.
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion
---

# Design Polish

Elevate a frontend project from generic to distinctive. This skill helps you avoid "AI slop" aesthetics by applying intentional design choices with consistent quality.

## Step 1: Assess the Current State

First, explore the project to understand:
- What framework/technology is being used (React, Vue, plain HTML, Tailwind, etc.)
- What pages/components exist
- The current visual state and what feels generic or lacking

Read the main entry point and key component files. Identify 2-3 files that would have the highest visual impact if improved.

## Step 2: Ask for Design Vibe

Use the AskUserQuestion tool to present these style options:

**Question**: "What vibe should this site convey?"

**Options**:

1. **Refined & Professional**
   - Clean, editorial feel with generous whitespace
   - Sophisticated typography with serif/sans pairings
   - Subtle animations, muted color palette with one accent
   - Feels like: Apple, Stripe, Linear

2. **Bold & Energetic**
   - High contrast, saturated colors, dramatic shadows
   - Strong geometric shapes, asymmetric layouts
   - Punchy animations with personality
   - Feels like: Vercel, Figma, Notion

3. **Warm & Approachable**
   - Soft edges, organic shapes, friendly colors
   - Playful micro-interactions, rounded corners
   - Comfortable spacing, inviting atmosphere
   - Feels like: Slack, Mailchimp, Airbnb

4. **Technical & Precise**
   - Monospace accents, code-inspired aesthetics
   - Dark themes with terminal vibes, sharp edges
   - Grid-based layouts, systematic color use
   - Feels like: GitHub, VS Code, Linear dark mode

## Step 3: Apply Design System Based on Vibe

Based on the user's choice, apply these specific guidelines:

---

### Refined & Professional

**Typography**:
- Display: Playfair Display, Newsreader, or Fraunces (serif)
- Body: Source Sans 3, IBM Plex Sans, or DM Sans
- Weights: 300/400 for body, 600/700 for headings only
- Size ratio: 1.33 (perfect fourth scale)

**Colors** (CSS variables):
```css
:root {
  --bg: #FAFAFA;
  --bg-subtle: #F5F5F5;
  --text: #1A1A1A;
  --text-muted: #6B6B6B;
  --accent: #2563EB;  /* or sophisticated green/burgundy */
  --border: #E5E5E5;
}
```

**Spatial composition**:
- Max content width: 720px for reading, 1200px for layouts
- Section padding: 80-120px vertical
- Generous line-height: 1.7-1.8 for body text

**Motion**:
- Subtle fade-ins on scroll (opacity + 20px translate)
- 0.3s ease-out transitions
- Avoid bounce/spring - keep it understated

---

### Bold & Energetic

**Typography**:
- Display: Clash Display, Cabinet Grotesk, or Space Grotesk
- Body: Inter (acceptable here), Satoshi, or General Sans
- Weights: Extremes - 200 vs 800/900
- Size ratio: 1.5+ (dramatic jumps)

**Colors** (CSS variables):
```css
:root {
  --bg: #0A0A0A;
  --bg-card: #141414;
  --text: #FFFFFF;
  --text-muted: #A1A1A1;
  --accent: #FF3366;  /* or electric blue, vivid orange */
  --accent-glow: rgba(255, 51, 102, 0.3);
  --border: #262626;
}
```

**Spatial composition**:
- Full-bleed hero sections
- Asymmetric grids, overlapping elements
- Tight spacing in clusters, dramatic gaps between sections

**Motion**:
- Staggered entrance animations (0.1s delay increments)
- Scale + opacity on hover for cards
- Gradient animations on backgrounds

**Backgrounds**:
```css
.hero-gradient {
  background:
    radial-gradient(ellipse at 20% 50%, var(--accent-glow) 0%, transparent 50%),
    radial-gradient(ellipse at 80% 20%, rgba(99, 102, 241, 0.2) 0%, transparent 40%),
    var(--bg);
}
```

---

### Warm & Approachable

**Typography**:
- Display: Bricolage Grotesque, Nunito, or Quicksand
- Body: Lato, Open Sans, or Nunito Sans
- Weights: 400/500 for body, 600/700 for headings
- Size ratio: 1.25 (comfortable reading)

**Colors** (CSS variables):
```css
:root {
  --bg: #FFF8F5;
  --bg-card: #FFFFFF;
  --text: #2D2A26;
  --text-muted: #7A756E;
  --accent: #E85D04;  /* warm orange, coral, or teal */
  --accent-soft: #FFF0E6;
  --border: #F0E6DE;
}
```

**Spatial composition**:
- Rounded corners: 12-16px on cards, 8px on buttons
- Comfortable padding: 24-32px on cards
- Breathing room between elements

**Motion**:
- Gentle bounce on buttons (transform: scale(1.02))
- Soft shadows that lift on hover
- Smooth 0.2s transitions

**Details**:
- Soft drop shadows: `0 4px 20px rgba(0,0,0,0.08)`
- Decorative illustrations or icons if appropriate
- Emoji or friendly iconography

---

### Technical & Precise

**Typography**:
- Display: JetBrains Mono, Fira Code, or IBM Plex Mono
- Body: IBM Plex Sans, Inter, or system-ui
- Weights: 400 for body, 500/600 for emphasis
- Monospace for data, labels, and navigation

**Colors** (CSS variables):
```css
:root {
  --bg: #0D1117;
  --bg-card: #161B22;
  --bg-elevated: #21262D;
  --text: #E6EDF3;
  --text-muted: #8B949E;
  --accent: #58A6FF;  /* GitHub blue, or green/amber */
  --border: #30363D;
  --syntax-keyword: #FF7B72;
  --syntax-string: #A5D6FF;
}
```

**Spatial composition**:
- Grid-based layouts with consistent gutters
- Compact, information-dense design
- Sharp corners (4px max) or none

**Motion**:
- Minimal - focus on instant feedback
- Subtle opacity transitions
- No playful animations - functional only

**Details**:
- Syntax highlighting inspiration for accents
- Terminal-style borders or code-block styling
- Tabular data presentation

---

## Step 4: Implementation Checklist

Apply these in order of visual impact:

### 1. Typography (Highest Impact)
- [ ] Add Google Fonts import for chosen font pairing
- [ ] Set font-family on body and headings
- [ ] Establish type scale with CSS variables
- [ ] Apply proper weights (avoid 400 vs 500 - use extremes)

### 2. Color System
- [ ] Define CSS variables in :root
- [ ] Apply background colors to body/sections
- [ ] Update text colors and muted variants
- [ ] Add accent color to interactive elements

### 3. Spacing & Layout
- [ ] Increase section padding for breathing room
- [ ] Establish consistent max-width for content
- [ ] Apply the vibe's spatial philosophy

### 4. Animation (Final Polish)
- [ ] Add page-load entrance animation
- [ ] Implement hover states with transitions
- [ ] Use animation-delay for staggered reveals

```css
/* Universal fade-in base */
.fade-in {
  opacity: 0;
  transform: translateY(20px);
  animation: fadeUp 0.6s ease forwards;
}

@keyframes fadeUp {
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.delay-1 { animation-delay: 0.1s; }
.delay-2 { animation-delay: 0.2s; }
.delay-3 { animation-delay: 0.3s; }
```

## Quality Standards (All Vibes)

Regardless of chosen style, every implementation must:

1. **Avoid Generic Defaults**
   - Never use: Inter (except Bold vibe), Roboto, Arial, system fonts as primary
   - Never use: Purple gradients on white backgrounds
   - Never use: Default shadows/borders without intentional styling

2. **Commit to the Aesthetic**
   - Every choice should reinforce the vibe
   - Inconsistency is worse than being boring

3. **Focus High-Impact Areas**
   - Hero sections and first impressions
   - Navigation and key CTAs
   - Card/content containers

4. **Test Dark/Light Appropriateness**
   - Some vibes suit dark themes, others light
   - Don't force dark mode if it fights the vibe

## Your Task

$ARGUMENTS

If no specific arguments, analyze the current project and:
1. Read key frontend files to assess current state
2. Ask the user for their preferred vibe
3. Apply the design system systematically
4. Focus on 2-3 highest-impact changes first
5. Ensure the result feels intentional and distinctive
