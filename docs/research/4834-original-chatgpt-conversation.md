Are there researchers that do work on how to translate the concept in the book essences and surfaces from Douglas Hospital into either machine learning ways of learning or LLM based conceptions or some combination of those two?












Yes. In fact, I think this is one of the most interesting research directions that is still surprisingly underexplored.
Surfaces and Essences argues that analogy is the fundamental operation of thought—that concepts emerge from seeing one thing as another, rather than from explicit logical rules. That idea sits somewhere between modern deep learning, symbolic AI, and cognitive science. There isn't a dominant research community explicitly saying "we are implementing Surfaces and Essences," but there are several groups that are converging on similar ideas from different directions.
The main camps are:
1. The Hofstadter lineage (closest philosophically)
This includes Douglas Hofstadter's earlier work and the Fluid Analogies Research Group.
Their systems such as Copycat, Tabletop, Letter Spirit, and Numbo attempted to model how concepts "slide" into one another through analogy instead of fixed symbolic rules. The architecture revolved around:
fluid concept networks
spreading activation
competing "codelets"
context-dependent concept activation
This is almost exactly the computational precursor to Surfaces and Essences. Google Books
Unfortunately this line of research became overshadowed by statistical machine learning during the 2000s.
2. Melanie Mitchell
If I had to recommend one researcher, it would be Melanie Mitchell.
She was one of Hofstadter's PhD students and worked directly on Copycat.
Her later work asks questions like:
What is abstraction?
How do concepts generalize?
Why are analogies difficult for AI?
What would genuine understanding look like?
She has argued repeatedly that today's LLMs exhibit impressive statistical behavior but still lack the flexible conceptual fluidity Hofstadter envisioned.
3. Kenneth Forbus and Structure Mapping
Kenneth Forbus built one of the major computational theories of analogy:
Structure Mapping Engine (SME).
Instead of embeddings, SME aligns relational structures:
Solar system : atom
becomes
planet ↔ electron
sun ↔ nucleus
based on relational correspondence.
This is much more symbolic than LLMs but is one of the classic computational models of analogy.
4. Dedre Gentner
Dedre Gentner developed Structure Mapping Theory.
She studies how humans actually learn concepts through analogy.
Many AI analogy systems are directly inspired by her work.
5. Modern LLM researchers
This area has exploded in the last few years.
People are studying questions like:
Do transformers internally represent concepts?
Where are analogies stored?
Are concepts vectors?
How abstract are LLM representations?
Recent work has identified internal "concept vectors" and investigated how analogical reasoning emerges inside transformer representations. arXiv
There is also work exploring LLMs specifically as engines for creative cross-domain analogy generation. arXiv
6. Representation learning
This is probably the closest ML equivalent to Hofstadter's ideas.
Instead of symbolic concepts:
Dog
Cat
Wolf
the model learns
■■■■■■■■■■■■■■■■
(a point in a very high-dimensional latent space)
Nearby vectors become nearby concepts.
Concepts become regions rather than definitions.
This is remarkably similar to Hofstadter's idea of fluid concepts.
7. Sparse Distributed Memory
Another researcher I think you'd enjoy is Pentti Kanerva.
His Sparse Distributed Memory represents memories as points in high-dimensional spaces.
Many people now see a connection between:
transformer embeddings
vector databases
associative recall
Kanerva memories
Interestingly, Hofstadter himself later said Kanerva's work was the closest thing he had seen to a computational realization of his own ideas about concepts. The New Yorker
Where I think the field is missing something
Based on many of our previous discussions about your autonomy architecture, I think you're aiming at something slightly different from what most of these researchers pursue.
Most current LLM work assumes:
text
    ↓
embeddings
    ↓
prediction
Whereas Surfaces and Essences suggests something closer to:
experience
      ↓
concept formation
      ↓
analogy
      ↓
prediction
      ↓
action
      ↓
new experience
      ↓
concept refinement
That's a continual learning system rather than just a next-token predictor.
It also aligns closely with the architecture you've described for your agent framework:
episodic memories
extracted causal principles
world-model schemas
retrieval by conceptual similarity
planning via analogical reuse
evaluator-generated learning after execution
Rather than treating embeddings as the concepts themselves, your approach treats them as an index into a richer symbolic and causal memory that is continuously updated through experience.
A research direction that I don't think has been fully explored
One idea that I haven't seen developed in the literature is something like:
Schema induction through analogical compression.

Imagine every completed task producing:
episodic trace
graph of events
causal relationships
outcome
learned principle
Then periodically performing analogical clustering:
Episode A
Episode B
Episode C
Episode D

↓

discover common relational pattern

↓

induce abstract schema

↓

store as reusable world knowledge
That is almost a direct computational realization of Surfaces and Essences integrated with modern LLMs and vector embeddings. It combines neural representations for similarity with symbolic schemas for abstraction and planning, rather than choosing one paradigm over the other.
Given your interests in causal principles, schema evolution, and autonomous learning agents, I think this intersection—Hofstadter's analogy-centered cognition plus continual neuro-symbolic schema induction—is one of the most promising and least crowded research areas today.

Google BooksarXivThe New Yorker









So what specific work would I wanna look at from Melanie Mitchell about that kind of concept and how it could be used in modern AI systems












Given what I know about the kinds of systems you're trying to build, I would actually read Melanie Mitchell's work in a different order than most people recommend. Rather than trying to understand her historical contributions, I'd focus on extracting architectural ideas that could inform a modern neuro-symbolic agent.
Here's the reading order I'd recommend.
1. Artificial Intelligence: A Guide for Thinking Humans (2019)
This is the single best place to start.
Not because it contains implementation details—it doesn't—but because she repeatedly explains what modern deep learning is missing.
As you read it, ask yourself:
Why can transformers interpolate but struggle to extrapolate?
What is abstraction?
Why is analogy difficult?
Why is compositional reasoning difficult?
Those questions are exactly the ones your architecture is trying to answer.
2. Copycat
This is the work I would spend the most time on.
The classic example is:
abc
↓

abd

then

ijk

↓

?
Humans answer
ijl
because they see the analogy
replace last letter
rather than memorizing characters.
Copycat is fascinating because it doesn't encode this rule explicitly.
Instead it builds temporary concepts dynamically.
Its important pieces are:
codelets
slipnet
workspace
temperature
conceptual slippage
If I were rebuilding it today I would almost certainly replace most of the symbolic search with LLMs while preserving the overall architecture.
3. Fluid Concepts and Creative Analogies
This is essentially the technical book behind Surfaces and Essences.
It explains:
how concepts activate
how concepts compete
how concepts merge
how analogies emerge
It isn't easy reading, but it's probably the closest thing to what you're looking for.
4. Mitchell's papers on abstraction
She has written several papers arguing that intelligence fundamentally requires abstraction and analogy rather than simply prediction.
Look particularly for papers discussing:
abstraction
analogy
generalization
ARC
Bongard problems
Those connect surprisingly well to today's benchmark discussions.
The thing I would "translate" into an LLM architecture
This is where I think there's a really interesting opportunity.
The original Copycat architecture looked something like this:
Perception

↓

Workspace

↓

Codelets

↓

Slipnet

↓

Analogy
Today I would imagine something more like:
LLM perception

↓

episodic memories

↓

graph of concepts

↓

vector retrieval

↓

schema induction

↓

planner

↓

execution

↓

reflection

↓

update graph
Notice that nothing about the architecture has fundamentally changed.
Only the implementation of each component has.
The biggest idea I'd steal
This is the idea I think deserves much more attention.
Copycat never assumed concepts were static.
Instead:
concept

↓

context

↓

activation

↓

meaning
Meaning changes continuously.
Modern LLMs already do something surprisingly similar.
A token embedding isn't its meaning.
Its meaning becomes whatever hidden-state trajectory develops during inference.
But today's systems typically discard that trajectory when the conversation ends.
Where I think the next step is
This is the part that intersects most closely with your work.
I'd replace the Slipnet with a continually learned semantic graph.
Instead of hand-built concepts:
Dog
Vehicle
Container
Agent
Obstacle
the system would continually learn new ones from experience.
Every completed task could produce:
episode

↓

analogies discovered

↓

new schema

↓

causal rule

↓

graph update

↓

future retrieval
That is remarkably close to how Mitchell has described human concept formation over the years, except today's LLMs provide a far more powerful perceptual front end than Copycat ever had.
If I were spending the next month on this
I'd focus on these, in order:
Copycat papers (understand the architecture)
Fluid Concepts and Creative Analogies (understand dynamic concepts)
Surfaces and Essences (understand the cognitive theory)
Artificial Intelligence: A Guide for Thinking Humans (understand the critique of current AI)
Recent ARC-AGI and abstraction papers (see how the ideas map onto modern foundation models)
One additional suggestion that I think would fit your interests particularly well is to read these alongside recent work from François Chollet on abstraction and ARC. Although Chollet and Mitchell come from different traditions, they converge on a central idea: intelligence is less about memorizing patterns than about constructing reusable abstractions that transfer to novel situations. The main difference is that Mitchell emphasizes analogy and fluid concepts, while Chollet frames the problem in terms of skill acquisition and generalization. Reading both together highlights complementary perspectives on the same underlying challenge.










So I will describe a way I’m thinking about this and you tell me if anybody is doing similar work or if this doesn’t make sense or doesn’t fit with the way that people conceptualize having LLM systems work on analogies it seems like I can just talk about a word or a concept and then ask an LLM to break apart all the types of attributes that could apply to that concept and then to it right through each of the instances of the types of attributes for that concept or Word then based on that, and maybe we build a graph of the words between the concept or word and the attributes and do some kind of semantic difference between each of them and Store that whole graph in bedding as a number as an in vetting basically then use that in fitting compared to embedding of other concepts or words that we’ve made and look at the difference between those and beddings as the similarity like that, using the cosine similarity to figure out the similarity of those concepts or words in an analogy space and closer things would be more analogous and further things would be less than analogous and I guess I’m thinking that if I could look for those in beddings that were closer between concepts, I could use that closeness to do something like making an analogy and to help guide how to deal with or think about one concept in terms of the second concept and that’s similarity could give me guidance about how to solve problems dealing with one concept because I can solve those problems in another similar concept or analogous concept




Show more







I think you're pointing toward something that is quite plausible, but I would modify it in a couple of important ways. The core intuition is sound, but I don't think the embedding should represent the concept itself. It should represent the concept's relational structure.
That's a subtle but important distinction.
What I think you're proposing
As I understand it, you're imagining something like:
Concept

↓

LLM extracts attributes

↓

LLM extracts relationships

↓

Graph

↓

Graph embedding

↓

Compare graph embeddings

↓

Find analogous concepts

↓

Transfer solutions
That actually resembles several active research areas, although nobody is doing exactly this combination.
1. Knowledge Graph Embeddings
This is probably the closest existing field.
People build graphs like
Dog

has-part → Tail
eats → Food
friend-of → Human
larger-than → Mouse
and then embed the graph rather than just the word.
Examples include:
TransE
RotatE
ComplEx
Node2Vec
GraphSAGE
The limitation is that these systems usually operate on fairly literal knowledge graphs rather than the rich, dynamic conceptual graphs you're describing.
2. Graph Neural Networks (GNNs)
Instead of embedding a word, researchers embed an entire graph.
Your idea of:
"Take the whole graph and turn it into one embedding"

is essentially what graph representation learning does.
The difference is that your graph would be generated by an LLM and include conceptual and analogical relationships, not just factual ones.
3. Neuro-symbolic AI
This is where your idea becomes more distinctive.
Most current neuro-symbolic systems use graphs primarily for reasoning after they're built.
You're proposing something more like:
LLM

↓

discover graph

↓

compress graph

↓

store graph embedding

↓

retrieve graph

↓

reason
That's closer to a learned semantic memory than a traditional knowledge graph.
4. Case-Based Reasoning (CBR)
There's an older AI field called Case-Based Reasoning that follows a similar philosophy:
Find similar problem

↓

Adapt previous solution

↓

Solve new problem
The weakness of classic CBR was that "similar" was usually hand-engineered.
You're proposing that an LLM could discover similarity automatically from conceptual structure.
Where I think I'd change your approach
This is the biggest thing I'd recommend.
Instead of asking the LLM for attributes, I'd ask it for relations.
For example, for a "tree," attributes might be:
green
wood
leaves
tall
Those don't capture much of what makes a tree useful as an analogy.
I'd instead ask for things like:
absorbs
branches
grows
anchors
transports nutrients
competes for sunlight
stores energy
provides habitat
Those are behaviors and causal roles.
Now compare that to an organization:
absorbs information
branches into departments
grows
anchors a community
transports information
competes for resources
stores knowledge
provides opportunities
Suddenly you have a much richer analogy. The similarity comes from relational structure, not surface attributes.
This is very much in line with Hofstadter's and Gentner's work on analogy.
I think you're missing one important level
Right now I think your hierarchy is approximately:
Word

↓

Attributes

↓

Embedding
I would make it:
Word

↓

Entity

↓

Relations

↓

Causal roles

↓

Graph

↓

Graph embedding

↓

Analogy
The causal roles are where a lot of the transferable reasoning lives.
One thing that particularly caught my attention
You said:
"use that closeness to help solve problems in another similar concept."

I think that's exactly the right goal.
But I don't think cosine similarity should be the final answer.
I'd use it only to retrieve candidate analogies.
Then I'd ask another model (or another stage of the same model):
"Which parts of these two relational graphs correspond, which don't, and what solution transfers?"

So the pipeline becomes:
Problem

↓

Build relational graph

↓

Graph embedding

↓

Nearest neighbors

↓

Retrieve top 20 analogies

↓

LLM aligns graphs

↓

Infer transferable causal principles

↓

Generate candidate solution

↓

Evaluate
That separation between retrieval ("what looks similar?") and mapping ("how do these structures actually correspond?") mirrors how both classic analogy research and many modern retrieval-augmented systems work.
Why I think this connects strongly to your architecture
From our previous discussions, you're already thinking about:
episodic memories,
extracted causal principles,
schema induction,
graph-based world models,
and continual learning from completed tasks.
What you're describing could become the retrieval layer for that architecture. Instead of retrieving memories based only on text similarity, you'd retrieve structurally similar experiences. Then the planner could reuse not just past actions, but the underlying causal schema that made those actions successful.
One additional idea I'd consider is representing each concept with multiple embeddings rather than one:
a semantic embedding (what it is),
a relational embedding (how it connects to other concepts),
a causal embedding (how it behaves and what effects it produces),
and perhaps a functional embedding (what goals it serves).
Different reasoning tasks could weight those spaces differently. For analogical reasoning, the relational and causal spaces are likely to be much more informative than the semantic space alone.










Yeah, so I think what I meant by first gathering all the types of attributes that could apply to a thing I was trying to get us. The idea that there are lots of things to concept can do like behave or be related to other concepts or have adjectives that describe a noun type of thing.  My intuition is that your formulation of using word in the relations, etc., is potentially too restrictive, but we may not have other ways of conceptualizing that that would relate to our current work in AI or machine learning.












I actually think you're identifying something deeper than I did in my previous response.
I implicitly assumed the ontology should be something like:
Entity
  ├── Attributes
  ├── Relations
  ├── Behaviors
  └── Causal roles
But I think you're asking a more fundamental question:
"What are all the dimensions along which a concept can be described?"

That's a different problem, and I think it's a richer one.
I think you're looking for a "concept schema"
Imagine the concept "tree."
Rather than saying
Tree

has-color
has-height
grows
you first ask:
What kinds of information could exist about anything?

Not just trees.
Maybe something like:
Identity

Properties

Parts

Relationships

Behaviors

Capabilities

Constraints

Goals

Inputs

Outputs

State

State transitions

Life cycle

Causes

Effects

Resources

Environment

Time

Scale

Ownership

Composition

Purpose

Failure modes

History

Potential futures

Similarity

Opposites

Examples

Counterexamples

Metaphors

Symbols

...
Now every concept becomes an instance of this much richer schema.
That is a very different idea from simply extracting relations.
I actually think this is closer to object-oriented programming
One analogy that came to mind is that concepts almost look like classes.
A class doesn't just have fields.
It has:
properties
methods
inheritance
interfaces
constraints
state
events
lifecycle
You're proposing something similar for concepts.
There are hints of this in AI, but I don't think anyone has unified it
Pieces exist.
Frame Theory (Marvin Minsky)
Frames were supposed to describe everything you know about an object.
A "restaurant" frame contained things like
waiter
ordering
paying
menu
expectations
That feels surprisingly close.
Ontologies
Things like OWL and Cyc attempt this.
But they're manually built.
They aren't learned.
Schema.org
Schema.org defines hundreds of properties.
Again,
manual.
Static.
Not learned.
Wikidata
Huge graph.
Lots of property types.
Still largely factual.
Not conceptual.
LLM function calling
Modern LLMs can already generate JSON.
Which means they can probably generate something like
{
  "properties": [],
  "behaviors": [],
  "constraints": [],
  "causes": [],
  "effects": [],
  "examples": [],
  "goals": [],
  "failure_modes": []
}
without much trouble.
Here's the part that I think is genuinely novel
Instead of hand-designing this schema forever...
Ask an LLM:
"What categories of information could exist about any concept?"

Then ask again:
"What categories are missing?"

Repeat.
Eventually you might get
Level 0

Concept

↓

Level 1

Properties
Relations
Behavior
...

↓

Level 2

Behavior

↓

Inputs
Outputs
Preconditions
Side effects
...

↓

Level 3

Inputs

↓

Physical
Informational
Social
Economic
...
Now you've induced a taxonomy of ways to describe concepts.
I haven't seen people explicitly build that hierarchy.
I think this connects to one of your long-standing ideas
You've often talked about wanting a world model rather than a collection of facts.
This feels like the metadata layer of that world model.
Not
"What do I know about trees?"

Instead
"What kinds of things can be known about anything?"

That's almost an epistemology rather than an ontology.
It's a model of the dimensions of knowledge.
Where I'd push it one step further
I wouldn't freeze those dimensions.
I'd let the system invent new ones.
Suppose it repeatedly encounters concepts where "reversibility" matters.
It might invent
Reversibility
as a new dimension.
Later it notices
Information reversibility
Chemical reversibility
Economic reversibility
Social reversibility
Now "reversibility" has become a first-class conceptual axis because experience showed it was useful.
That feels much closer to how humans seem to build increasingly rich conceptual structures over time.
This is also why I think Surfaces and Essences is relevant. Hofstadter argues that concepts are not fixed containers of attributes; they are flexible structures whose important dimensions depend on context and analogy. Your intuition is similar, but with an additional twist: instead of assuming we already know the right dimensions, you're asking whether an AI could discover and refine the dimensions themselves. I don't know of a mature AI architecture that does exactly that. There are related ideas in frame systems, ontologies, concept bottleneck models, and representation learning, but they generally assume the descriptive dimensions are given. The idea of continually learning how concepts should be described—not just what is true about them—still feels like an open research direction.










Or I could be conceptually, hallucinating, or having fuzzy thoughts












I don't think you're conceptually hallucinating. I do think you're compressing several different research questions into one intuition, which makes it feel fuzzy.
One way to separate them is:
Question 1: What is a concept?
This is a representation problem.
Examples:
embedding
graph
symbolic frame
latent vector
probabilistic program
Machine learning has many answers to this question.
Question 2: How can a concept be described?
This is the question I think you're gravitating toward.
Not
"What is a tree?"

but
"What kinds of statements can be made about anything?"

That feels closer to epistemology than representation learning.
Question 3: Which aspects matter for this task?
Humans don't activate everything they know about a tree.
If you're building a house:
wood
strength
grain
matter.
If you're studying ecology:
habitat
carbon cycle
species interactions
matter.
If you're writing poetry:
symbolism
age
seasons
matter.
A good AI has to dynamically choose which dimensions of a concept are relevant.
Question 4: How do analogies emerge?
Once you've represented concepts richly enough,
Tree
and
Corporation
may become similar because both
grow
branch
transport resources
compete
develop structure
have lifecycle stages
The analogy isn't discovered because "tree" and "corporation" have similar word embeddings. It's discovered because their relational organization overlaps.
That distinction is central to decades of analogy research.
Where I think your intuition is strongest
The part that caught my attention wasn't "attributes."
It was when you said something like:
"First discover all the possible ways something could be described."

That sounds almost like trying to learn the basis vectors of conceptual space.
In linear algebra, we ask:
What basis vectors span this vector space?

You're asking:
What basis vectors span the space of human concepts?

That's a very different question from learning embeddings.
One caution
I would be careful not to assume that there is one universal set of dimensions.
Human concepts are extremely context dependent.
Take "bank."
Depending on context:
financial institution
river bank
blood bank
bank shot in billiards
aircraft banking
No finite ontology is likely to capture all uses perfectly.
Modern LLMs partly solve this by making representations contextual. The representation of "bank" changes depending on surrounding text.
So I suspect the answer isn't:
Learn one gigantic schema.

It's more likely:
Learn a large library of possible conceptual dimensions, then activate the subset that best explains the current context.

A thought experiment
Here's a test I would try.
Take 100 random concepts.
For each one, ask an LLM:
"List every distinct category under which this concept can be described. Don't describe the concept yet; describe the categories of possible descriptions."

Then cluster those categories.
I wouldn't be surprised if you ended up with a few hundred recurring dimensions.
That would give you an empirical starting point instead of trying to design an ontology from first principles.
Ironically, that's a very machine-learning way to approach what has traditionally been a symbolic AI problem.
So my assessment is:
I don't think the intuition is confused.
I do think it's currently underspecified.
The next step is to turn it into a precise research question.
A formulation that feels precise enough to investigate is:
Can a learning system automatically discover the latent dimensions along which humans organize concepts, and use those dimensions to improve analogical reasoning, transfer learning, and planning?

That's a question that could be explored experimentally, rather than just philosophically.