# GenAI-Intel V2 - What We've Built So Far

## 🎯 The Big Picture

Think of this system like a research assistant that:
1. **Finds** startup funding announcements every week
2. **Figures out** which cloud provider (AWS/GCP/Azure) each startup uses
3. **Figures out** which AI provider (OpenAI/Anthropic/etc.) each startup uses
4. **Stores** all the evidence so you can see WHY it made those conclusions
5. **Tracks** changes over time (like if a startup switches from AWS to GCP)

---

## 📦 What We've Created (3 Files)

### 1. **ARCHITECTURE.md** - The Blueprint
This is like the architectural blueprint for a house. It shows:
- How data flows through the system (like plumbing diagrams)
- What each component does (like "kitchen," "bedroom")
- What rules we follow (like building codes)

**Key Decisions:**
- Always prefer facts over guesses (your "Deterministic > LLM" philosophy)
- Store every piece of evidence, not just the final answer
- Track everything over time to spot trends

---

### 2. **models.py** - The Data Templates
Think of these as **standardized forms** that ensure data is always consistent.

**Example:** When we find a funding announcement, we fill out this form:
```
Company Name: [___________]
Funding Amount: $[___] Million
Round Type: [Seed/Series A/B/C]
Date: [___________]
Investors: [___________]
```

These forms prevent mistakes like:
- ❌ "fifty million" vs. ✅ 50 (always numbers)
- ❌ linkedin.com/company/X vs. ✅ company.com (always real websites)
- ❌ Random date formats vs. ✅ Standard YYYY-MM-DD

**What's Inside:**
- `FundingEvent` - Template for funding announcements
- `AttributionSignal` - Template for one piece of evidence (like "DNS shows AWS")
- `Attribution` - Template for final conclusion (like "90% confident it's AWS")
- `Startup` - Template for company information

---

### 3. **schema.sql** - The Database Structure
This is the filing cabinet where everything gets stored.

**Think of it like organizing a physical office:**

📁 **Cabinet 1: Startups**
- One folder per company
- Contains: Name, website, industry, description

📁 **Cabinet 2: Funding Events**
- One folder per funding round
- Contains: Amount, date, investors, article links

📁 **Cabinet 3: Evidence (Attribution Signals)**
- Individual pieces of evidence
- Example entries:
  - "DNS shows AWS CloudFront" - STRONG evidence
  - "Job posting mentions GCP" - MEDIUM evidence  
  - "Website footer says 'powered by AWS'" - WEAK evidence

📁 **Cabinet 4: Daily Snapshots**
- One per company per day
- Shows current conclusion: "Today we think they use AWS (85% confident)"

📁 **Cabinet 5: Weekly Reports**
- Tracks each weekly run
- Shows: How many startups found, any errors, how long it took

**Built-in Analytics:**
The database automatically calculates:
- "How many startups use AWS vs GCP vs Azure?"
- "Which evidence sources are most reliable?"
- "Which startups changed cloud providers recently?"

---

### 4. **database.py** - The Librarian
This is like a librarian who knows exactly where everything is stored.

Instead of you having to know database commands, you just ask:
- "Store this new startup" ✅
- "Find me all signals for Anthropic" ✅
- "What's the latest attribution snapshot?" ✅

**It handles the technical details so the rest of the code stays simple.**

---

## 🔍 How the Evidence System Works (Non-Technical)

Imagine you're a detective trying to figure out if a company uses AWS:

### Step 1: Gather Clues (Signals)

**Strong Clues (Weight: 1.0)**
- 🏆 Official press release: "Partnership with AWS"
- 🏆 DNS records show amazonaws.com
- 🏆 Company blog post: "We migrated to AWS"

**Medium Clues (Weight: 0.6)**
- 🔍 Job posting: "AWS experience required"
- 🔍 Tech blog mentions AWS tools

**Weak Clues (Weight: 0.3)**
- 🤔 Website footer mentions "cloud"
- 🤔 CEO tweet mentions cloud computing

### Step 2: Score the Evidence

Add up the weights:
- 1 Strong clue = 1.0 points
- 2 Medium clues = 1.2 points
- 3 Weak clues = 0.9 points

**Total: 3.1 points → "STRONG" confidence that they use AWS**

### Step 3: Store Everything

We save:
- ✅ The conclusion ("AWS")
- ✅ The confidence level (90%)
- ✅ Every individual clue
- ✅ Where each clue came from

**Why store every clue?**
So you can:
- See exactly why the system made that conclusion
- Debug if something seems wrong
- Update weights if certain clues prove unreliable

---

## 🎨 What Makes This System Special

### 1. **Transparent, Not a Black Box**

❌ **Bad System:**
"I think this startup uses AWS"
(No explanation why)

✅ **Our System:**
"This startup uses AWS (90% confident) because:
- DNS shows amazonaws.com (STRONG)
- Job posting requires AWS certification (MEDIUM)
- Security page mentions AWS (WEAK)"

### 2. **Facts First, AI Second**

**Priority Order:**
1. Check if there's an official partnership ✅ (100% reliable)
2. Check DNS/server records ✅ (very reliable)
3. Check job postings ✅ (pretty reliable)
4. Check website content ✅ (somewhat reliable)
5. Ask AI to make educated guess ⚠️ (last resort)

**We only use AI when there's no hard evidence available.**

### 3. **Tracks Changes Over Time**

Not just "What do they use now?" but:
- What did they use last month?
- Did they switch providers?
- Is their usage getting more or less entrenched?

Example:
```
Jan 1: AWS (MODERATE confidence)
Feb 1: AWS (STRONG confidence) ← They're getting more committed
Mar 1: AWS (STRONG confidence)
```

### 4. **Shows Its Work**

Like a student showing work on a math problem:
```
Question: What cloud provider does Anthropic use?

Work shown:
- Official partnership announcement (GCP) = 1.0 points
- DNS CNAME points to Google = 1.0 points  
- Total = 2.0 points

Answer: GCP (STRONG confidence)
```

---

## 🚀 What's Still Missing (To Build Next)

### Phase 1: Discovery Module
**What it does:** Finds funding announcements automatically
- Checks TechCrunch RSS feed
- Checks Google News
- Uses AI to extract structured data from articles

### Phase 2: Resolution Module  
**What it does:** Figures out company websites
- Tries to find it in the article
- Guesses common patterns (company-name.com)
- Uses AI search as backup

### Phase 3: Attribution Engine
**What it does:** The detective work!
- Checks DNS records (technical, but automated)
- Searches for job postings
- Parses website content
- Scores everything and makes conclusion

### Phase 4: Main Pipeline
**What it does:** Orchestrates everything
- Runs weekly automatically
- "Find funding → Resolve website → Attribute providers → Store results"
- Handles errors gracefully
- Sends you a summary

---

## 💭 Analogy: What We've Built So Far

Imagine building a restaurant:

✅ **ARCHITECTURE.md** = Floor plan and workflow diagrams
✅ **models.py** = Standardized recipes and ingredient lists  
✅ **schema.sql** = The walk-in refrigerator organization system
✅ **database.py** = The inventory manager who tracks everything

🚧 **Still need to build:**
- The actual kitchen equipment (discovery/attribution modules)
- The cooking procedures (main pipeline)
- The reservation system (automation)

---

## ❓ Questions to Consider Before We Continue

1. **Data Sources:** Do you have access to Crunchbase API, or should we rely on free sources (RSS feeds)?

2. **AI Usage:** We're using Claude for:
   - Extracting funding info from articles ✅ (structured extraction, very reliable)
   - Resolving websites when not found ✅ (web search, pretty reliable)
   - Tie-breaking when evidence is weak ⚠️ (last resort only)
   
   Is this balance right for you?

3. **Automation:** Should we set this up to run:
   - Weekly automatically (GitHub Actions - free)
   - On-demand when you trigger it
   - Both?

4. **Outputs:** What reports do you want?
   - Excel export of all startups with attributions?
   - Weekly email summary?
   - Dashboard you can check anytime?
   - All of the above?

---

## 🎯 Next Steps

Once you're comfortable with what we've built, I'll create:

1. **Discovery module** - Finds funding announcements
2. **Resolution module** - Figures out websites  
3. **Attribution engine** - Does the detective work
4. **Main pipeline** - Ties everything together
5. **Setup guide** - How to actually run this

Each piece will have clear comments explaining what it does in plain English.

**Ready to continue? Any questions about what we've built so far?**
