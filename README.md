# Whispers in the Court — Europa Universalis V

A mod for **EU5 1.3.11**, inspired by the CK3 mod *Voices of the Court*. Talk
with your court, summon the council, send envoys to foreign courts and travel
your realm. The AI of the **Player2** app answers, and what comes of it reaches
the game as real events and consequences.

## Getting started

1. Install and open the **Player2** app, and log in.
2. Download **`WhispersInTheCourt.exe`** from the **Releases** page of this
   repository, put it anywhere and start it. Nothing
   else to install: it installs (and later updates) the mod in EU5's mod
   folder by itself, as **"Whispers in the Court (USE THIS)"**: enable that one
   in the EU5 launcher. If you also subscribed on the Steam Workshop, leave
   the Workshop copy off - only the installed one hides the purple debug
   boxes of debug mode, which depend on your version of the game.
3. The first time, the Court Brain window asks you three things:
   - **the language** the characters, the events, the chronicle and the
     advisor speak to you in - written in your own words (English, Italiano,
     Español, Deutsch...). The interface stays in English;
   - **how often unprompted events** come (see below);
   - **the difficulty** (see below).
   Then, if you like, you can write your own **AI instructions**.
4. Press **Start EU5** (it starts the game with `-debug_mode`, which the mod
   needs: without it EU5 cannot load the texts Court Brain writes). If you prefer
   to start the game from Steam, add `-debug_mode` to its launch options (right
   click Europa Universalis V > Properties > Launch Options). Enable **Whispers in the Court (USE THIS)** in the EU5
   launcher the first time, and load your game. After a few seconds the record says
   **"The game answers: the bridge works."** and the "Game" dot turns green.

Windows may show a SmartScreen warning ("unrecognised app") because the exe is
not signed: "More info" → "Run anyway".
The exe is built from this repository's source by GitHub Actions, and each
release lists its SHA-256 (see [SECURITY.md](SECURITY.md)). You can also run
Court Brain straight from the sources, with no exe at all
(see [docs/DEVELOPING.md](docs/DEVELOPING.md)).

Keep the Court Brain window open while you play: closing it stops Court Brain.

## How it works

1. In the game, press one of the mod's buttons:
   - **Speak with** on a character's panel, yours or foreign;
   - in diplomacy with another country, among the *friendly actions*:
     **Send an Envoy**, **Request a Meeting**, **Pay a State Visit**;
   - on your own country: **Summon the Council**, **Hear an Estate**,
     **Royal Progress**, **Issue a Decree**;
   - **Answer in person…** on one of the mod's events;
   - the **Court** button at the top left: council, decree, chronicle, and an
     audience with the spokesmen of each estate of your realm;
   - the **AI Advisor** button, beside Court: see below.
2. The **court panel** slides in on the right of the screen, drawn like one of
   EU5's own windows. It already knows who is before you and how the realm
   stands.
3. Write (or dictate) what your ruler says. If you want ideas, click
   **"Suggest a few replies"** under the conversation: three are written for
   you, only when you ask (it saves AI credits). When you send for someone (a character, the council, an estate,
   your envoy) you speak first; when someone comes to you, they do.
   The length of an answer follows what you ask: a greeting gets a few words,
   an ordinary question two to four sentences, and if you ask to explain, to
   tell, to report or to say why, you get a real, full answer - still spoken,
   with what that person knows and what they have only heard.
   People answer the person, not only the words: they react to your tone, push
   back, bargain, want something for themselves, and change as the talk goes
   on. Court Brain keeps a record of each scene so nobody turns into a set of
   tics: a gesture, a slip or a pet phrase is used once, the title ("Sire") is
   not in every speech, and the mood of the day shows once and then only
   colours the tone.
   Nobody stays a faceless name: the first time someone speaks in the panel,
   their role appears beside their name; in narrations and events, anyone who
   appears for the first time, or returns after a long while, is introduced in
   the sentence itself.
   Every event the mod shows in the game (stories, chronicle pages, audiences,
   outcomes, decrees, battles) opens with a line **"In short: ..."** saying what
   happened and, if there are choices, what you must decide; the full text
   follows below it.
4. **What is decided takes effect when you press Dismiss.** Nothing is judged
   while you talk: a small line under the conversation reminds you of it, and
   when you give an order a quiet "Noted" appears. When you dismiss them, the
   whole conversation is judged once - everything you decided in it, each
   decision once (if you changed your mind, your last word counts) - and the
   consequences come with the event that follows. Judging once instead of after
   every order uses far fewer AI credits.
   In dealings with other countries (envoy, meeting, visit), what **you** say
   plainly ("I declare war on you", "from today we are allies", "let there be
   peace", or a plain yes to what they proposed) happens with the outcome, if
   the game and their court allow it. It costs no court influence, but what the
   real act would cost: a war, stability and prestige (more without a real
   cause); a peace or a truce, prestige; an alliance or a guarantee, government
   power; breaking an alliance, prestige, stability and that court's trust. If
   the game refuses it, an event the next day explains why, and nothing is paid.
   What they proposed and you never accepted is nothing. Grave acts - a new form
   of government, a killing, a deposition - happen only if you ordered them in
   so many words: Court Brain checks your exact words before applying them.
   A war is not declared in an audience at court: it is done through an envoy
   or a meeting with that country.
5. Press **Dismiss**: the panel closes and the **outcome event** arrives in the
   game: the page a biographer of the ruler, a contemporary, wrote for that day -
   where and who, what was at stake and decided (with words really spoken), how
   it was taken, what was done that same day, what the court and the town said
   by evening. Nothing random, and no hindsight about what came later. The consequences happen when you press **"So be it."**: hovering over
   it, the game itself lists what changes (stability, gold, estates, modifiers
   and for how long, wars declared…), and below you find "Whispers in the Court
   asked the game for: …" to compare. Anything requested that is missing from
   the game's list was not applied (for example for lack of court influence).

**People, not the game.** In a conversation two AIs do two different jobs.
The one that plays the people never sees the game's figures, labels or the
list of possible consequences: it gets the realm as the court knows it ("the
chest won't last the year", "the barons are restless", "the ruler has just
named a new adversary") and plays the people - what's on their mind, news as
news, advice as people of that age would give it, with no talk of "rivals",
"stability" or "twenty-two gold". A second one, the referee, reads the
whole conversation when you dismiss them, with all the rules and numbers, and
turns what was really decided into the game's consequences - and it is only
called when something was decided (an order, a grant, a promise, a deal), so
small talk costs no extra tokens. Both can be adjusted in the instructions editor ("In the
world, not in a game" and "The referee of consequences").
Nothing happens in the game until **you** decide: a counsellor urging "I say we
pay for the repairs now" is advice; the consequences come only when you say
yes, order it or refuse it in your own words. Before the referee is called, a
second, separate check reads the conversation and must find in your own lines,
word for word, the order, grant, refusal or yes that makes each decision; a
greeting, a question, a remark or "I understand your concern" never becomes an
edict.

**How the people are written.** The dialogue follows what good writers do:
scenes start in the middle of people's lives (no welcoming speeches, no
agenda read aloud); every speaker wants something from the moment and plays it
- needling, soothing, testing, dodging, selling - with a concrete detail of
their own life or trade rather than "matters of the accounts"; what matters
most is often left unsaid; they talk to each other, not only to you; nobody
explains what the listener already knows or comments on your words instead of
answering them; and a speech ends on something still alive.

**A council that governs.** The council and the ministers get a dossier drawn
from the game's own figures: the real troubles of the state (a deficit and how
many months the chest lasts, debts, unrest, a discontented order, few men to
raise, an open succession...) and what is sound - so they never invent a crisis
the realm does not have. They know the instruments the state really has
(ordinances on taxes, trade, production, levies, control of the land...;
edicts; bargains with an order; laws) and propose reforms as administrators
would: in plain words a player who is no expert understands, saying what the
measure touches, who pays and who gains, what it costs now and later, how long
before it shows, what can go wrong. Their skill shows in how good the proposal
is. The treasurer may give a rounded figure from his books; nobody reads out a
report.

**A court that changes with the age and the size of the realm.** Court Brain
reads from the game, every time, where the world and the realm stand:
- the AGE as the game has it (Traditions, Renaissance, Discovery, Reformation,
  Absolutism, Revolutions), which institutions have really appeared in the
  world (the printing press, banking, the New World...) and which the realm has
  taken up, and the great situations under way (the Black Death, the Hundred
  Years' War, the Reformation, the wars of religion...). People, things,
  weapons, offices, money and news belong to that age - and nothing arrives
  before this world has reached it, even if it is ahead of or behind history;
- the SCALE of the realm, from its holdings, its rank and its standing: a
  lordship (the ruler knows the reeves by name; matters are a mill, a toll, a
  feud), a small realm, a regional power, a great realm, an empire (viceroys,
  subject rulers, colonies, ministries, ambassadors of powers; news arrives
  late and coloured). Who comes to court, what stories are about and how big a
  consequence is follow the scale, and so do the subject rulers, colonies,
  overlord and great-power standing the game shows.
The realm's profile is rewritten whenever the realm changes in kind - a new
age, a new scale, a new name, faith, government or capital - so the court of a
county that became an empire, or of 1340 that became 1560, never keeps talking
like the old one. The Life and the history of a century are told through the
ages they crossed and how the realm grew or shrank.

**The world pushes back.** At every difficulty, nothing is free or certain:
every measure has a price and someone who pays it, orders are carried out as
well as the realm can manage, and a wise decision can still go wrong. The
referee pairs a reform's gain with its cost; what comes of it later is weighed
on the realm's real state; the difficulty sets how hard the world pushes. So
that a campaign of two hundred years never falls into a pattern, the stories
remember the kinds they have told most often in the whole campaign and leave
them alone, never retell the latest ones with new names, and vary the shape of
the choices - sometimes every choice costs, sometimes the tempting one is a
trap.

**The royal biographer.** The page of the day is written by a real person of
your court, named at the top of it: the royal biographer. Each one is a
different kind of writer - a cleric who sees God's hand everywhere, a dry notary,
a flattering humanist, an old soldier, a court gossip, a sharp widow, a
physician... - with their own bias and their own voice. They never say what
they think ("for my part...", "I judge..." are forbidden, and a page that slips
is rewritten once): their view shows only in the telling - what they choose to
tell and leave out, the order, the words, a telling detail - and now and then
it colours a page more strongly. They grow old and may die; the court then
finds another, whose first page says so.
The outcome event has **"Speak with the royal biographer..."** (it is also in
the Court menu): praise them, ask what the court is saying, have a page
**corrected** - a flatterer obeys at once, a proud scholar may argue or refuse,
and what they rewrite goes into the book - or **dismiss** them and say what
kind of writer you want next.

**The book of your life.** Outcome events and chronicle pages also have **"Read
the book of my life, as written so far..."** (and the Court menu has it too):
Court Brain shows the Life of your ruler up to today, at most 10,000
characters, told by the biographers in their own voices - where the pen changed
hands, the book says so. Only what is new since you last read it is written, so
reading it again costs no tokens. When your ruler dies an event brings the
biographer's short account of their life and works; from it (or from the Court
menu, "Read the Life of the late...") you can ask for the **whole Life**, written
from every note of the reign. It is a real biography, not the daily pages put
one after another: the biographer first plans the life into its periods (a
war, a long quarrel with the barons, the plague years, the old king and his
heir) with the line that runs through them, then writes it part by part, in
order - each part a chapter with its own title, telling its years as stories
with causes and consequences, from the ruler as they were when the book
begins to a last paragraph that closes the life. That uses **many AI tokens**,
once: afterwards the finished book can be read again for free.

**Even if you hardly use the AI.** The book does not depend on your
conversations. Court Brain also takes down, with no AI at all, what the game
itself shows: wars begun and ended (who against whom, who attacked), peace,
alliances and rivalries made and broken, subjects gained and lost, becoming
or ceasing to be someone's subject, new laws, the ruler's marriage, a new heir,
land won and lost, a new capital, faith or government, loans, the game's own
events by their titles - and once a game year the realm's accounts in one line
(treasury, stability, prestige, army, fleet, population, holdings, allies,
estates' satisfaction...). A player who plays the game normally and never opens
a conversation still gets a Life of every ruler, the account at their death
and the history of each century: the biographer reads those registers as a
historian would, telling what the numbers meant, never inventing the talk
behind them. Where you did use the court, both are woven together.

**A hundred years of the realm.** Every hundred years of a campaign an event
arrives: the biographer of the day offers to write and read to you the history
of the realm over that century - its rulers one after another, its wars and
peaces, lands won and lost, faith, money and people. It is written the same
way, planned and then told in chapters, in the biographer's voice. Choose "Hear
the history of the century (uses many AI tokens)" or "Another time"; it
stays in the Court menu to be read later, and once written it is free to read
again. It is written from the annals - the weighty notes of every reign (wars,
pacts, decrees, great choices), kept in `biography.json` and never read by the AI
until then - and from the biographer's account of each reign; once the history
is written, those annals are deleted.
The notes the Life is written from live in their own file (`biography.json`,
beside the campaign's memory): one short line per audience, decree, choice,
battle or pact. The AI never reads them except when you ask for the book, so
they cost nothing the rest of the time. Once the whole Life of a dead ruler is
written, its notes are deleted and only the book remains (the court's own
memory keeps its usual summary of the reign). Loading an older save rewinds the
book too. Story events with choices do not offer the book: pressing it would
mean skipping the choice.

**Sending for someone.** In any conversation, ask for someone to be brought in
("send for the treasurer", "call Father Anselmo here", "summon the castellan of
Pesaro"): whoever is at court or nearby comes in at once and takes part in the
conversation; someone elsewhere in the realm arrives some days later, in an
event of their own; a foreign ruler or the dead cannot come, and someone tells
you why.

**Promises and pacts.** Every real agreement made at court is remembered and
watched in the game - and the other side acts on it by its own decision:
- **war together**: Aragon swears to join your war on France; the moment you
  declare it (through the mod or from the game's own menus), Aragon's envoy
  arrives, and if Aragon honours its word it really enters the war beside
  you. Or it stalls, or betrays you;
- **defence**: if you or your ally are attacked, the other is called on; if
  your ally goes to war, you are asked to keep your side - and refusing has a
  price;
- **peace and truces**: going to war with someone you swore peace to breaks
  your word, and everyone hears of it;
- **partitions and peace terms**: when the war you agreed on ends, the terms
  come due - who took what; a partner who cheated you over a partition may
  hand you a just cause for war against them;
- **money and men**: tributes, subsidies, loans, troops by the spring: when the
  deadline comes, did they deliver? They pay, stall or refuse;
- **at home**: a privilege to an estate in exchange for its loyalty or its
  money, "no new tolls for ten years", a charter, a pardon. Break it with a
  decree or a choice and the estate reacts: protests, an ultimatum, a closed
  purse, and - if they are angry and strong - a rising.
What the other court does happens in the game (entering a war, declaring war,
breaking or making an alliance, making peace, paying, giving you a casus
belli) and is listed in the tooltip of every choice of the event, marked as
their own decision. Your reputation follows you: every court and estate knows
how your past promises ended, and weighs your word accordingly.

**Decrees.** Write the text of the decree: nobody comments, the chancery drafts
it in your realm's own form and "Proclaim" puts it into force (the consequences
with "So be it" on the event). The reactions come later, in their own time:
someone at court asks for an audience, a chronicle page tells how the decree
is received. The AI weighs who may do this in your form of government
(absolute, feudal, with a parliament, republic, theocracy, tribe) and how strong
the Crown is. A secure ruler pays little; a weak one, or one who bypasses
parliament, pays more. A decree does not change a law of the realm by itself.

**AI Advisor.** Outside the roleplay: it talks to you directly, with numbers.
Ask how the realm stands, what happened in a period ("what happened between
1340 and 1350?": it reads the whole memory of the campaign for those years and
never invents what is not there), strategy advice, the real history behind what
you see. It changes nothing in the game.

**Decrees and reforms really change the game.** Besides edicts, the AI has
twelve levers of the economy and the state (taxation, production, trade,
prosperity, coinage and inflation, army, fleet and building upkeep,
construction, control, levies, food supply), each as a minor, moderate or major
advantage or disadvantage for 5, 10 or 20 years: real modifiers of the game,
visible in the country panel.

**Works of the realm.** Order it plainly - in a decree, to the council, to a
minister - and it is done in the game itself: build, enlarge, reduce or pull
down a building (a castle, a university, a marketplace... or "every
fortification in the realm"), lay a road, grant a town its charter, convert
the faith or the people of a province, accept or tolerate a culture, grant or
revoke an estate privilege, enact or repeal a law, adopt or drop a government
reform, move the capital, invest in a place's development, order or
prosperity. Court Brain finds the work in your own game's catalogue and the
places on its map; the AI never writes the game's commands. Every work has its
price: the game's own (a building is paid for and built over months) and the
realm's (money, stability, the estates who lose by it - pulling down the
castles angers the nobles), sized by how much of the realm it touches, and
paid only if the work really happens. A work that strikes at a strong estate
may meet resistance in the days after: the order stands, the barons drag their
feet, a guild asks who will defend its shops. A work that cannot be done (a
place not yours, a building that does not exist) is refused on the spot, and
the court is told what does exist.

**Changing the state itself.** When events justify it (a union, a great
victory, a doge who makes himself lord, a change of faith, a conquest) or when
you ask for it with a decree, the AI may propose a **new name** (with its
adjective), a **new form of government** (monarchy, republic, theocracy, tribe)
or a **new rank** (county, duchy, kingdom, empire). It happens with "So be it"
and really changes the nation, as the game's own conversions do: reforms,
succession and mechanics, the same price (−50 stability, −25 legitimacy), 20
years before another change; impossible in a personal union or bankruptcy. A
new rank follows the game's requirements. If something is not possible, an
event the next day explains why. New names survive reinstalling the mod.

**Power over people and crises of the realm.** When the story leads there, and
only if it is believable, the AI may propose: killing someone (executed,
assassinated, a fever, vanished), deposing the ruler, crowning someone, naming
an heir or a regent, putting someone in or out of the cabinet, exiling them.
And the realm can split: an estate rises in the regions that would really
follow it, as a brewing revolt or a **civil war** that breaks out at once, made
like the game's own risings. Plots, coups and revolts unfold over several
stages; at the decisive moment the event lets you choose a side, and one choice
may be to **take the rebels' side**: the civil war breaks out and, as soon as
the rebel country exists, the mod runs the game's `tag` command and you go on
playing them (debug mode only). The ruler does not change sides: they stay at
the head of the Crown, now your enemy. Without a cause of your own, risings,
coups and civil wars are **rare**; they need a realm truly in crisis or
something you provoked.

**Stories.** Now and then something happens: a surprise feast of the nobles,
a plot to investigate, rebels, bandits, a crime, the consuls of a town with a
petition, a quarrel among common folk, a miracle, the heir in love with the
wrong person... always in keeping with the state of the realm. Unprompted
stories are of four kinds, in this order of frequency: **affairs of
government** (decisions only the ruler can take), **life of the realm**, **the
ruler's daily life** and, more rarely, **affairs from abroad** (embassies,
marriage offers, foreign nobles asking for help or proposing a plot, exiles,
border skirmishes, spies, pirates - only when the real situation gives a
reason). A story event has **three choices**, each with its own consequences in
the button's tooltip, plus **"Answer in your own way…"**, which opens the panel.
Each story lasts as long as it really would, and many come back weeks later
with the next stage.

**War.** While the realm is at war, the court tells the war - but only what
really happened in the game. Court Brain reads the battles (every army
remembers where and when it last won, and who led it), the losses in battle
and to hunger and sickness, the sieges (ours, the enemy's, our allies'), and
the land occupied on both sides; when the army suddenly comes back smaller, it
knows there has been fighting and asks the game for the full reports (at most
once every ten minutes, so a war does not become a stream of saves). From
this, at the war's own pace:
- **pages of the war**: a soldier's deed near the place of a real battle, a
  siege held or stormed, townsfolk in an occupied town, the wounded brought
  home - always naming the real place and battle ("after the fight near
  Montefeltro on 26 February");
- **matters of the army**: pay in arrears, captains quarrelling before a
  siege, sickness in the lines, deserters, a daring plan - with three choices
  and real military effects for a year or two: army morale, discipline,
  siegecraft, army supply, marching speed, morale recovery, army tradition,
  war exhaustion, manpower.
People and small deeds may be invented; battles, sieges and occupations never.

**Command a battle.** Open one of your battles (click the fight on the map)
and press the new **Command** button beside the retreat button, or use the
army's **Command the Battle** ability while it fights. The game pauses and the
side panel opens on the field as it really stands: a narrator outside the
battle walks both lines - the ground, the river, the weather, who commands
each side and how well, how many men are in the fighting line and how many are
held back, foot, horse and guns, how each wing stands. Then you give your plan
in your own words (three possible plans are offered, sound or not - they are
not hints), or leave it to the general.
- The plan is **judged against that field** by a commander's eye: the ground,
  the numbers, the enemy, the troops you have, and whether your general can
  carry it out. A good plan earns an edge, a poor one costs you - on the armies
  there, only while the battle lasts (heart, discipline, the general's grip on
  the field, how well horse, foot and guns are used; sometimes a blow struck at
  once: men lost, morale broken or restored).
- The game then **runs by itself at the slowest speed**, and **pauses when the
  battle needs you**: the first clash, a wing giving way, the reserve thrown
  in, a long grind, the edge of a rout. Each moment comes as an event with
  three orders for *that* moment - **what they do stays secret until the next
  moment shows it** on the field - and a fourth: **"I will see to this
  myself"**, to give your own order in the panel.
- If the ruler leads the army in person, orders can put the ruler in danger:
  riding to the front heartens the men, and can end in a wound - or, rarely,
  worse.
- When the battle ends, an account tells how it was decided and what your
  orders really did, with the true losses; it goes into the chronicle, the
  court's memory and the book of your reign.
Everything is rolled and applied by Court Brain from a closed set of the mod's
own effects; how much a plan can gain or cost follows the difficulty.

**Unprompted events.** In the Court Brain window, under "Unprompted events"
(or in Settings), choose how often the court brings something new on its own:

| Setting | A new event about every |
|---|---|
| None | never |
| Once a year | 330–400 days of game time |
| Rare | 140–200 days (5–7 months) |
| Calm | 90–140 days (3–5 months) |
| Normal | 50–90 days (2–3 months) |
| Frequent | 30–50 days |

What comes new grows, most of the time, out of something really in your realm
and your campaign - what you have built, what you have lately learnt, a law in
force, a way of the age you have taken up, the currents of the world, your own
past decisions - so the court of a realm of guilds in 1340 does not sound like
that of a realm of manufactories in 1800.

**Room to play.** Whatever the court brings on its own - a new story, the next
stage of one under way, a consequence coming back, a page of chronicle, someone
at the door - never piles up: after each one some days of game time pass
before the next (Once a year 30, Rare 28, Calm 22, Normal 18, Frequent 12), and
after a conversation of your own a few more (20 / 20 / 15 / 12 / 8). What
matures meanwhile waits its turn and comes one at a time. Only an ally called on
to keep a pact answers sooner, and a consequence that has waited two months
comes before anything new.

The pace you choose is kept however busy your reign is: the consequences of your
own deeds and the next stages of stories come on top of it, never instead of it
(when a new event is due, it goes first and they wait). A panel of Court Brain
you leave open while you go back to playing (untouched for a couple of minutes
while two weeks of game time pass) is closed on its own, as if you had closed it,
so it cannot hold the court silent. An event that never got its answer holds up
the stories for a month at most.

**Land changing hands.** Land can be ceded, sold, exchanged, given as a dowry
or handed over to be held under another banner, when both sides really agree
in the conversation (or a story's choice is that agreement): to you, from you,
or between two other realms. The referee sees the provinces each realm holds,
by their exact names; Court Brain finds the towns of the place on the map and
checks in the last save that the giver holds them. The panel shows "Land: ...
passes from ... to ..., as a core, fully integrated", and when you confirm the
outcome the game moves every town the giver still holds: the old owner's core
removed, the new owner's added, fully integrated. A land meant to become a
vassal passes to its overlord, who can then release it from the game's own
interface. A proposal, a price still being argued or land the giver does not
hold moves nothing.

**Characters, not functions.** Everyone who speaks - your court and the
foreign rulers you visit, meet or send envoys to - has a persona written once
and kept: temperament, what they want and fear, a voice nobody else has, a few
lines in their own words, and three living details a novelist would give them
(a story they like to tell, a sore point, a habit, someone they love or cannot
forgive). For real historical figures these come from history. Now and then a
scene lets one of those details surface in the middle of business - the one
shown least recently, never twice in a scene, and each rests for most of a
year before it may come back, so nobody turns into the same anecdote. People
change: every few years of game, those you keep meeting have what they want
and fear NOW and their details brought up to date from what happened to them
(a grief, a grudge born or settled, a new pride), their voice unchanged. Their
abilities in the game show in words beside their names ("brilliant at
administration, poor in war") and in how good their advice and their
bargaining are. At a foreign court the people present - a baron, a bishop, a
secretary - cut in when it touches them.

**Negotiations.** Envoys, meetings, state visits and the estates negotiate
from their own interests: they name a price, counter, sweeten or harden, bring
in the people present, and never ask again a question you have answered -
they take the answer and move.

**The court keeps track.** Before a story goes on to its next stage, or a
consequence planned earlier arrives, Court Brain checks it against everything
that happened since: if a conversation, a decree or a pact already settled it,
it does not come back as if still pending (the story closes, the planned event
is dropped); if it has moved, it comes back as it stands now. Whatever you set
in motion - letters and envoys to other courts, an inquiry, an order carried
out far away - always comes back with its answer when it would really be
known. At the end of a conversation, the matters it settled are closed for
good. And consequences are only what happens now, in the right direction and
size: sending envoys to ask for money costs the journey, the money (if any)
comes with the answer.

**Someone asks for an audience - always for a reason.** Nobody is sent at
random. First the court looks for someone with a real reason to come now,
from the true troubles of the realm, what it remembers (promises, grudges,
pending matters, your decisions) and their office; the visit must bring a
decision worth making or something you need to know, never the same matter
twice. If nobody has such a reason, nobody comes.

**The consequences of what you do do not depend on that setting: the AI always
decides them.** At the end of every conversation, decree and story that closes,
the AI decides whether what happened will bring something later (someone you
slighted comes back to complain, a promise is thrown back at you, a favour
bears fruit, an order meets resistance) and when, as it really would; the
record says "follow-up expected in N days". It comes even with "None", and
stories already begun go on.

**Difficulty.** Chosen at the first start, changeable in Settings:

| Difficulty | What changes |
|---|---|
| Easy | A friendly court. What hurts you weighs one step less (major → moderate, moderate → minor); risings need a truly desperate realm. |
| Normal | The court as designed. |
| Hard | A demanding court: people bargain hard and remember slights; minor losses become moderate, the greatest gains are tempered; crises start sooner. |
| Very hard | An unforgiving court: every loss weighs one step more, every gain one step less; rivals strike when you stumble; crises start early. |

**AI instructions.** The "AI instructions" button opens an editor with two
parts:
- **Your instructions**: boxes where you tell the AI, in your own words, how
  the characters should talk, what events you want, how things should be
  narrated, facts about your world and your ruler, what to avoid, how your
  suggested replies should sound and how the advisor should answer you;
- **Built-in (advanced)**: the instructions Court Brain itself gives the AI, in
  full - the rules for dialogue, narration, stories, decrees, plots and
  rebellions, the advisor - which you can rewrite. "Reset to default" brings
  back the original.
Changes take effect with the next thing the AI writes. Whatever the text says,
Court Brain still enforces the rules of the game: what may be proposed, what
the game can apply, the format of the answers.

**A reign that keeps changing.** The court is built for long campaigns:
every new story is given a different tone and a different person at its
centre from the ones used lately, sometimes a twist; stories grow out of old
ones and people return older, richer, bitter or grateful; the age itself moves
(plague years, schisms, new weapons, printing, new worlds). When your ruler
dies the court marks the new reign, and old loyalties and promises are tested;
every generation (30 years) the portrait of your realm - its titles, customs,
fears and pride - is rewritten from its own history.

**What the AI knows.** A snapshot at every scene (date, treasury, stability,
estates, court, ruler); a live picture of the world about every minute while
you play (wars, allies, rivals, subjects, overlord, great powers, neighbours);
and every 20 minutes the full state papers, read from a save the game makes for
it. At first meeting it writes a personality for each character, which it then
keeps, and a profile of the realm, so that France does not sound like the Maya.

**Light on tokens, heavy on memory.** Court Brain sends Player2 only what
each request needs: the rules of narration go with the outcome of a
conversation (its turns never narrate), the laws and privileges of the realm
with the council, the estates and decrees - and in any other conversation the
moment the talk turns to them; stage directions already used are not sent
again, and in a long conversation the older exchanges become a short recap.
Memory is kept in layers: the recent past in full; older facts folded into a
chronicle of the reign that never grows past a fixed size (recent years in
detail, old reigns in a line); and the complete journal, from which the court
**recalls** exactly what is needed - say a name, a place or a year from decades
ago and the old grudge, promise or event comes back into the scene. The
advisor gets the recent record plus whatever older records touch the question
(and the complete record of any years you ask about).

**Saves and memory.** Every game gets a campaign number, written inside the
game itself; every save carries the exact point of the court's memory it
belongs to. Load an older save and the court remembers only what had happened
up to then; play a different future from there and the two futures stay
separate. Different games never mix.

**No empty events.** A save can hold an event of the mod still unanswered, or
still waiting on the calendar. Every scene is numbered, and the number is kept
in the save; Court Brain keeps the words of the recent scenes by number, and
after a load or a restart it puts them back before you answer. If the words of
a scene are really gone (a much older save), the event says the matter is set
aside, and its choices and consequences are not offered - nothing happens that
nobody wrote. And if the game ever fails to load the words of a new scene, the
scene is sent again, and never shown without them.

**Standing measures.** Some orders are meant to last: a literacy drive, a permanent
watch on the markets, a standing school of gunners, a new levy on salt. Order one "from
now on" (in a conversation or a decree) and it stays in force until you revoke it:
- It is a real, permanent modifier of the game (for a literacy drive: literacy grows
  faster and higher), listed among your country's modifiers under its own name.
- Nothing lasting is free: it is paid **every month with a share of the Crown's tax
  income** - 3%, 6% or 10% by its strength - so its cost grows as your realm grows,
  and it shows in the game's budget.
- A measure meant to make money (taxes, trade, production, the coin) pays for itself:
  it never costs the income it raises.
- A new levy brings a share of tax income instead (3%, 6% or 9%), and always weighs on
  something, a step lighter than the levy (prosperity, trade, control...). Any measure may also weigh on something besides
  money when it really would (schoolmasters taken from the fields: production), and a
  real start-up cost is paid once when it begins.
- To end it, just say so ("abolish the literacy drive") to your council or in a decree:
  it stops at the next event, and so does its cost. You can also make it larger or
  smaller the same way. Up to four stand at once; the court and the referee always
  know which are in force.
- **Years on, it is put to the test.** About a year or two after a lasting gain begins
  (a standing measure, or a policy or edict of five years and more: income, levies,
  morale, faith, anything), and now and then after that, a story about it comes: those
  who pay for it resist, its officials grow lazy, the money runs short... Sound choices
  keep it, sometimes even strengthen it; a careless or wrong one loses it.

**Words are not deeds.** Sweet-talking the AI does not work. What counts is what
you actually order or offer, not how convincingly you put it:
- **This world only.** What exists follows the age your game has reached, from
  1337 to the 1800s: muskets are impossible in 1340 and ordinary in 1640, and if
  your game is ahead of real history, the game wins. Nothing from fiction exists
  (no aircraft, no machine guns), and nothing supernatural answers: you can order a rite to
  summon demons, but what follows is only how the Church, the court and the people
  take it. Such orders bring no gain in the game, only people's reactions.
- **Substance over rhetoric.** Before any consequence, the referee restates your
  order in plain terms (who does what, with what means). People weigh offers and
  interests, check your claims against what they know, and ignore words aimed at
  the game ("the rules say you must accept").
- **Honest odds.** Every order gets odds from concrete things: money, men, time,
  who carries it out, who opposes it, how well the Crown is obeyed. Ordinary orders
  within your power are simply carried out. When there is a real risk, it can go
  wrong: the costs stay, the gains are lost, and you hear later what happened. A
  decree shows its odds before you proclaim it.
- **Treaties need the other court.** Peace, alliances, truces, submission and land
  given to you are decided by the numbers (strength, their own wars, rivalry, how
  far your word is trusted), not by an envoy's "yes": an envoy talked into
  something is overruled by their court. Land can only be given by its lord.
- **Land has a price.** What a piece of land is to its lord is weighed from the
  map: a realm's heartland is not for sale (only war or overwhelming power takes
  it), but a small holding far from it can be bought for a large sum or an
  exchange of land, a bigger one for a very large sum. Allies and empty treasuries
  sell cheaper, rivals not at all; a greedy ruler sells cheaper, a proud one
  dearer. The court you talk to knows this and bargains on it.
- **Tricks have a memory.** Trying the same failed thing again gets harder each
  time. Lies, forgeries and false flags can come to light later, and outrages and
  exposed deceits make your word worth less for a while, abroad and at home.
- **Facts are the game's.** Everyone tells apart what the game shows (wars,
  alliances, rivals, neighbours and their strength), what a person concludes from
  it ("I would not trust Granada"), and what nobody can know. Nobody invents a war,
  a threat or a claim as fact, and nothing is decided on something that did not
  happen. Foreign courts know their real neighbours and whether they are at war,
  so their reasons come from the real map.
- **You are never limited to the suggestions.** The suggested replies are only
  ideas: write anything - propose, bargain, make a counter-offer, refuse. A deal
  like "help me against England and I help you against Granada" is recorded as a
  pact, and the court watches whether each side keeps its word.
- **Only your words are yours.** What you type is always read as your own speech:
  lines written for other characters or fake "system" notes change nothing.

**Consequences with a reason.** Before a story or a page of chronicle reaches
you, what each choice would do in the game is looked at a second time against
the story and the realm as it really is: a consequence the story does not
explain is dropped, the others get the size the matter really has (a quarrel in
one town is never a major change; putting something off never brings a large
gain).

## What you need

- **`WhispersInTheCourt.exe`** and the **Player2** app.
- EU5 1.3.11 started with **`-debug_mode`** ("Start EU5" does it). In debug
  mode achievements are disabled.
- EU5 in any of its languages: the mod's interface stays in English, and
  Court Brain reads the language the game runs in and installs the mod's texts
  for it.
- EU5 in **windowed or borderless fullscreen**, not exclusive fullscreen,
  otherwise the panel cannot appear over the game.
- The purple "DEBUG INFO" boxes debug mode adds to tooltips, and the error
  platypus at the bottom right, are **hidden** by the mod. To see them again,
  from the console: `effect set_global_variable = votc_show_debug`; to hide them
  again: `effect remove_global_variable = votc_show_debug`.

Settings live in `%LOCALAPPDATA%\WhispersInTheCourt\` (`config.json`,
`instructions.json`); campaign memories and the log in the `court_brain` folder
beside your saves ("Open the memory folder").

## Balance

- The AI **writes no code**: it chooses consequences from what the mod and the
  game can do, and Court Brain writes the game's commands.
- **Every consequence is the most plausible one**, never a fixed recipe: it
  follows from what was done, from the realm as it is (who gains, who loses,
  who can resist, how strong the Crown is) and from the difficulty.
- **What helps you costs court influence** (25 a month, 100 at most) and a
  scene brings at most 3 such gains. The price of a deed does not depend on your
  influence - but it is **in proportion**: one price where a measure really bites,
  smaller than its gain when the measure is sound; a decree that works leaves the
  realm better off. An estate's satisfaction moves only when the deed touches its
  interests, by as much as it does (often a slight 2%, not always 10%), and an
  estate struck lately is struck more softly. A page of chronicle, where you had no
  choice, brings only slight effects - unless a blow of fate (a plague, a flood).
- **Ordinary statecraft is not an outrage.** A war on a rival or with a real cause,
  a peace, an alliance, a realm that submits to you: nobody rises over it. Before
  judging a war, the AI is told how your realm would take it (a rival, an ally's
  enemy, a broken promise, another faith; or a realm weary of war, short of men or
  money), and a war on a declared rival costs no stability to declare. A realm
  that comes under your crown by agreement makes yours proud (prestige, stability).
- **Grave deeds** - a massacre, a sacrilege, a betrayal, a tyranny - are judged
  again as the whole realm and the world would take them: every estate, the
  common people, the council, the army, the Church and the courts abroad. They
  can bring anything the game has, up to its heaviest: stability and
  legitimacy collapsing, estates in fury, risings and civil war, an attempt on
  your life or your throne (rolled, with its odds), foreign outrage, broken
  alliances, causes for war and war itself, people dying or fleeing, and plots,
  ultimatums and interdicts in the weeks after (risings and plots only for a
  real outrage). Even on easy, an atrocity is never cheap. An ordinary decision costs nothing extra, and nothing is shown.
- War, peace, alliances, claims and submission happen only by **your
  decision** (said plainly, and weighed when you dismiss them), checked by Court Brain before
  and by the game when they fire. If they fail, you are told why and pay
  nothing.

## Using Google Gemini instead of Player2

Court Brain uses Player2 by default. You can connect it instead to **Google Gemini**
with a free key of your own. Everything else works the same; voices and dictation
still go through the Player2 app.

1. Open https://aistudio.google.com/apikey, sign in with a Google account, press
   **Create API key** and copy it.
2. In Court Brain press **The AI** (at the bottom of the window), choose **Google
   Gemini**, paste the key and press **Save**. Leave the model as it is:
   `gemini-3.1-flash-lite` allows the most play a day on the free key (bigger Flash
   models write better but allow only a few requests a minute and a few dozen a day).
3. The record says "AI: Google Gemini, model …" and the **AI** light turns green.

Good to know: the exact free limits of your key are shown at
https://aistudio.google.com/rate-limit. If you type a model name that does not exist,
Court Brain picks the closest one and says so in the record. On the free plan Google
may use what is sent to improve its products. Your key is kept only in Court Brain's
settings on your computer: never show it in screenshots. **Back to Player2:** The AI →
Player2.

## Using OpenRouter instead of Player2

**OpenRouter** gives you hundreds of models (Gemini, DeepSeek, Claude, GPT, Qwen,
Mistral...) with one key, paid with credits you add on OpenRouter.

1. Open https://openrouter.ai/keys, sign in, add some credits if you want paid models,
   press **Create Key** and copy it.
2. In Court Brain press **The AI**, choose **OpenRouter**, paste the key and press
   **Save**. The default model, `google/gemini-3.1-flash-lite`, is the one Court Brain
   is tuned on and costs a few cents an evening. To use another, write its name as
   OpenRouter shows it (`maker/model`, e.g. `deepseek/deepseek-v4-flash`); models
   ending in `:free` cost nothing but are slower and limited.
3. The record says "AI: OpenRouter, model …" and the **AI** light turns green.

If the model name does not exist, Court Brain says so in the record and uses the
default. Stronger models write better scenes but cost more per request.

## If something goes wrong

- **The record never says "The game answers".** Check that the mod is enabled
  in the launcher and that EU5 was started with `-debug_mode`.
- **The panel does not appear.** Put EU5 in windowed or borderless fullscreen.
- **Player2 does not answer.** Open the Player2 app and log in.
- **Events show "The text of this ... did not arrive from Court Brain".** Almost
  always EU5 was started without `-debug_mode`: close it and start it with the
  **Start EU5** button (or add `-debug_mode` to the launch options in Steam).
  Court Brain notices it by itself: it warns you in its window and with an event
  in game, and brings no new events until the game is restarted with it.
  Otherwise, in the launcher enable only **Whispers in the Court (USE THIS)** -
  not a Workshop copy, not a copy unpacked by hand - and restart EU5.
- **AI credits.** After each conversation the record says what it cost: requests,
  tokens and joules (read from your Player2 balance). Every request - its kind,
  the model you use, its tokens and joules - is also written to `ai_usage.jsonl`
  in Court Brain's folder, so you can see what uses them.
- At every start the record lists the errors the game reported about the mod
  in the previous session, if any.

## Limits

- Windows only, single player only.
- `-debug_mode` is required, so no achievements.
- The mod's invisible bridge runs `run votc_poll.txt` every 3 seconds: you will
  see those lines in the console history.
- EU5 does not let mods add a category to the diplomacy menu: the mod's
  actions sit among the *friendly actions*.
- A memory made *after* the last save is in no file: if you quit without
  saving, the court will not remember it, like the game.
- Court Brain's own save every 20 minutes weighs a few hundred MB and freezes
  the game for a couple of seconds.
- "Answer in person" exists on the mod's events, not on vanilla ones.

## For developers

Whispers in the Court is open source under the [MIT license](LICENSE).

- [docs/DEVELOPING.md](docs/DEVELOPING.md): what is where, running Court Brain
  from the sources (Python 3.10+, no packages), the generators, the checks and
  the build;
- [docs/ARCHITETTURA.md](docs/ARCHITETTURA.md): how the game and Court Brain
  talk to each other;
- [SECURITY.md](SECURITY.md): what Court Brain connects to, what it reads and
  writes, and how to check a release;
- [CONTRIBUTING.md](CONTRIBUTING.md): reporting bugs and sending changes;
- [NOTICE.md](NOTICE.md): what the license covers, and what belongs to Paradox.

In short:

```bat
cd tools\court_brain
py -3 -m courtbrain --install
py -3 -m courtbrain
```

To build the exe: `py -3 tools\build_exe.py` (writes `dist\WhispersInTheCourt.exe`).

*Whispers in the Court is a fan-made mod, not affiliated with or endorsed by
Paradox Interactive. Europa Universalis is a trademark of Paradox Interactive AB.*
