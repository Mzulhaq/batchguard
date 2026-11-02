# Career fit: data engineering and backend foundations

BatchGuard demonstrates a focused set of Python and data-engineering fundamentals: defining a file contract, validating malformed and inconsistent data, detecting duplicates and conflicts, preserving row lineage, and publishing exact aggregates transactionally. It is useful portfolio evidence for discussing these skills. The project alone does not establish professional experience, production operations experience, or eligibility for a particular role.

## Example skills reference

On November 2, 2026, SpaceX's careers site listed a **Data Engineer (Starlink)** role in Hawthorne, California. The posting describes work on data hygiene: identifying bad, missing, duplicate, and stale records; building validation and remediation; and creating reusable checks and documentation. It also lists Python or another modern language and SQL, while requiring at least one year of professional data or software engineering experience (internships count). BatchGuard offers a small project example for discussing validation, duplicate handling, auditability, and reusable rules. It does not integrate an AI model, work with a CRM, handle stale data over time, or establish the listed experience requirement. This is a skills reference, not a recommendation or fit guarantee. Check the current posting for changes: [SpaceX Data Engineer (Starlink)](https://job-boards.greenhouse.io/spacex/jobs/8626500002).

## How to discuss the project

Two additional employer postings were checked on the same date. [Capgemini's Junior Python Developer role](https://careers.capgemini.com/job/Iasi-Junior-Python-Developer/1427338033/) in Iasi lists Python, relational SQL, testing, Git, documentation, and Docker/CI exposure. BatchGuard demonstrates those fundamentals; the posting also requires professional experience and French/English, so this is a skills comparison rather than a candidate recommendation.

[Showpad's AI Engineer role](https://job-boards.greenhouse.io/showpad/jobs/8264241) in Bucharest lists Python, model experience, LLMs and cloud services, with retrieval/search and generative-AI evaluation as additional skills. BatchGuard covers reliable data preparation and Python testing only. A future document-search project with a reproducible retrieval evaluation would address a separate portfolio gap; this project does not claim those AI capabilities.

Explain how the quality threshold behaves at its boundary, why a blocked batch publishes no new facts, and how a failed SQLite transaction can be retried safely. Show how source and order ID define fact identity, why identical normalized rows do not double count, and why reports and quarantine have different raw-data exposure. The project demonstrates data and backend foundations; it makes no claim of model integration, business impact, production scale, or individual job eligibility.

The resume drafts in the [interview guide](interview-guide.md) are starting points only. Adapt them to your actual contribution after understanding and exercising the project, and be transparent about AI assistance.
