"""The team of agents for one business (names match the design plan).

Manager agent  -> manager.py        the steps the AI agent (Grok Bot) runs; the agent talks with the owner
1. Interviewer -> interviewer.py    learns business needs
2. Planner     -> planner.py        picks topics and schedule, writes scripts
3. Creator     -> creator.py        makes text, images, videos
4. Owner approval -> owner_approval.py  human checks each post
5. Publisher   -> publisher.py      posts on schedule, replies
6. Analyst     -> analyst.py        finds what works, sends learnings to the Planner
"""
