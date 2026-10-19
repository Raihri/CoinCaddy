# Design System : SME Finance Copilot

## 1. Design Direction & Philosophy

Build a calm, trustworthy, modern finance workspace for Bangladeshi SME owners, bookkeepers, and operators. The interface should feel practical, legible, and authoritative before it feels decorative.

### Core Principles

1. **Clarity Over Decoration** — Financial status and next actions must be easy to scan instantly. Avoid eye-strain from unnecessary ornamentation.

2. **One System Across the Product** — Shared navigation, spacing, typography, surfaces, controls, and status semantics across every screen.

3. **Progressive Detail** — High-level summaries first; supporting records, explanations, and actions available one level deeper.

4. **Evidence Before Confidence** — Distinguish imported facts, calculated figures, estimates, forecasts, and AI suggestions clearly.

5. **Solid & Grounded Visuals** — Zero gradients, zero glassmorphism, and zero neon effects. Rely on crisp solid surfaces, sharp typography, and subtle solid borders (`#E2E7EF`).

6. **Accessible by Default** — High-contrast text, clear focus states, keyboard support, and status indicators that do not depend on color alone.

## 2. Visual Language & Color Tokens

### Overall Composition

* **Canvas:** Neutral, light-gray application background (`#F5F7FA`).

* **Desktop Layout:** Persistent left navigation sidebar (`240px` wide) and a primary responsive workspace container.

* **Card Styling:** Solid white cards (`#FFFFFF`) with 1px solid borders (`#E2E7EF`), `12px` border radius, and `20px` internal padding. Avoid drop shadows except for floating menus/overlays.

* **Header Alignment:** Clear title on the left, primary action button aligned to the right.

### Solid Color Token Palette

*Gradients and semi-transparent glass fills are strictly prohibited across all UI components, buttons, card fills, and charts.*

| Token Name | Solid Hex Value | Usage / Role | 
| ----- | ----- | ----- | 
| `canvas` | `#F5F7FA` | Overall application background | 
| `surface` | `#FFFFFF` | Primary card, modal, and table backgrounds | 
| `surface-muted` | `#EEF2F6` | Secondary panels, table headers, inset containers | 
| `text-primary` | `#172033` | Primary headings, body copy, and key metric numbers | 
| `text-secondary` | `#667085` | Subtitles, labels, table headers, metadata | 
| `border` | `#E2E7EF` | Structural borders, card outlines, table dividers | 
| `primary` | `#2347A5` | Primary action buttons, active tab states, key links | 
| `primary-soft` | `#EAF0FF` | Active navigation item background, soft highlights | 
| `success` | `#16845B` | Paid status, reconciled entries, positive cashflow | 
| `success-soft` | `#E8F5E9` | Background tag for success status badges | 
| `warning` | `#B76E00` | Due soon, action required, low cash alerts | 
| `warning-soft` | `#FFF8E1` | Background tag for warning status badges | 
| `danger` | `#C0393B` | Overdue invoices, failed syncs, destructive actions | 
| `danger-soft` | `#FFEBEE` | Background tag for danger status badges | 
| `info` | `#2876B8` | Draft status, informative tips, neutral system notes | 
| `info-soft` | `#E3F2FD` | Background tag for informational status badges | 

## 3. Typography & Numerical Rules

### Typeface Specification

* **Primary Google Font:** `Inter` (Fallback: `Plus Jakarta Sans`, sans-serif).

* **Global Letter Spacing Token:** `-0.06em` (`letter-spacing: -0.06em`) applied to headings, numerical balances, navigation, and primary UI labels for a tight, precise, technical financial appearance.

* **Tabular Numerals:** Apply `font-feature-settings: "tnum"` / `font-variant-numeric: tabular-nums` to all monetary amounts (`৳` / `BDT`), percentages, quantities, and aligned table data columns to ensure perfect vertical alignment.

### Type Hierarchy Scale

| Element | Size | Weight | Letter Spacing | Line Height | 
| ----- | ----- | ----- | ----- | ----- | 
| **Page Title** | `26px` (`1.625rem`) | `600` (SemiBold) | `-0.06em` | `1.2` | 
| **Section Title** | `18px` (`1.125rem`) | `600` (SemiBold) | `-0.06em` | `1.3` | 
| **KPI Value** | `32px` (`2.0rem`) | `700` (Bold) | `-0.06em` | `1.1` | 
| **Sub-heading / Card Title** | `15px` (`0.9375rem`) | `600` (SemiBold) | `-0.04em` | `1.4` | 
| **Body Text** | `14px` (`0.875rem`) | `400` (Regular) | `-0.02em` | `1.5` | 
| **Small Label / Table Header** | `12px` (`0.75rem`) | `500` (Medium) | `-0.02em` | `1.4` | 

## 4. Spacing, Grid & Radius System

Follow a strict **4px base spacing grid**: `4, 8, 12, 16, 20, 24, 32, 40, 48px`.

* **Card Padding:** `20px` desktop / `16px` mobile.

* **Component Gap:** `8px` – `12px` between adjacent inputs, icons, and buttons.

* **Card Grid Gap:** `16px` – `24px` between dashboard grid items.

* **Section Gap:** `24px` – `32px` separating main workspace sections.

* **Border Radii:**

  * Controls, inputs, and buttons: `8px` (`rounded-lg`)

  * Summary cards, modals, and tables: `12px` (`rounded-xl`)

  * Status pills and badges: `16px` (`rounded-full`)

## 5. Application Shell & Navigation

### Desktop Layout Structure

* **Sidebar (`240px` fixed width):**

  * Brand identity block at top.

  * Vertical menu list with icon + label (`14px`, `-0.06em` spacing).

  * Active state: `#EAF0FF` background, `#2347A5` text/icon color, and a `3px` solid `#2347A5` indicator bar on the left edge.

  * Settings and account profile sticky at the bottom.

* **Main Workspace:**

  * Full-width scrollable viewport with a max content container width of `1280px`.

  * Top header bar with global search, date range picker, and user notifications.

### Mobile Layout Adaptation

* Sidebar collapses into a side-drawer triggered by a top-left hamburger menu button.

* Multi-column card grids stack into a single responsive column.

* Key financial summaries remain pinned at the top.

## 6. Component Patterns

### Summary / KPI Cards

* Background: Solid `#FFFFFF`, Border: 1px solid `#E2E7EF`.

* Layout: Top label (`#667085`), middle prominent balance (`#172033`, tabular nums, `-0.06em`), bottom comparison trend indicator.

* *Example:* `৳ 4,50,000` — "vs last month (+12%)".

### Solid Data Visualization (Charts)

* No gradient area fills underneath line charts. Use crisp solid stroke lines (`2px` thickness) in `#2347A5`.

* Bar charts use solid vertical bars in `#2347A5` (Actuals) and dashed outlines or hashed fills in `#2876B8` (Forecasts).

* Always display explicit axis labels, currency identifiers (`৳ BDT`), and a simple solid legend.

### Data Tables

* Header row: `#EEF2F6` solid background with `#667085` uppercase/medium text (`12px`).

* Data rows: `#FFFFFF` background with `1px` solid `#E2E7EF` bottom borders.

* Text fields (Party name, invoice ID) left-aligned; Monetary amounts right-aligned with `tabular-nums`.

### Semantic Status Badges

* **Paid / Reconciled:** Text `#16845B` on Solid `#E8F5E9` background.

* **Due Soon / Review:** Text `#B76E00` on Solid `#FFF8E1` background.

* **Overdue / Failed:** Text `#C0393B` on Solid `#FFEBEE` background.

* **Draft / Pending:** Text `#2876B8` on Solid `#E3F2FD` background.

## 7. SME Finance & Copilot Specifics

### Data Provenance & AI Suggestions

* **Fact vs. AI Forecast:** Always label AI suggestions with a solid "AI Suggested" badge (`#2876B8`).

* **Confirmation Steps:** Never alter financial records automatically; require explicit user review (`Confirm` / `Dismiss`).

* **Currency Format:** Always prefix Bangladeshi Taka with `৳` and format numbers using local standard groupings (`৳ 1,00,000`).

## 8. Quality Assurance Checklist

* \[x\] Font set to Google Font with `-0.06em` letter spacing applied to key visual anchors.

* \[x\] Zero gradients used across cards, navigation, buttons, and charts.

* \[x\] Tabular numerals enabled for all currency (`৳`) and numeric data.

* \[x\] Solid high-contrast colors used for accessible status badges.

* \[x\] Fully responsive multi-column to single-column stacking verified.