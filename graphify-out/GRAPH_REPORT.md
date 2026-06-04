# Graph Report - vitegrid  (2026-06-03)

## Corpus Check
- 39 files · ~425,599 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 580 nodes · 1454 edges · 29 communities (22 shown, 7 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 149 edges (avg confidence: 0.56)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `1b254128`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- [[_COMMUNITY_Agent Layout & Style Pipeline|Agent Layout & Style Pipeline]]
- [[_COMMUNITY_Document PDFDocx Parser Engine|Document PDF/Docx Parser Engine]]
- [[_COMMUNITY_Frontend UI Components & State|Frontend UI Components & State]]
- [[_COMMUNITY_Backend FastAPI API & DB Storage|Backend FastAPI API & DB Storage]]
- [[_COMMUNITY_Layout Audit Programmatic Tests|Layout Audit Programmatic Tests]]
- [[_COMMUNITY_Frontend Package Configuration|Frontend Package Configuration]]
- [[_COMMUNITY_Frontend TypeScript Config|Frontend TypeScript Config]]
- [[_COMMUNITY_Image Pipeline Integration Tests|Image Pipeline Integration Tests]]
- [[_COMMUNITY_Remediation & Correction Tests|Remediation & Correction Tests]]
- [[_COMMUNITY_Parser V2 PDF Parsing Tests|Parser V2 PDF Parsing Tests]]
- [[_COMMUNITY_Frontend Node build tsconfig|Frontend Node build tsconfig]]
- [[_COMMUNITY_VLM Coordinate Transformations|VLM Coordinate Transformations]]
- [[_COMMUNITY_Frontend Docx Validation Metrics|Frontend Docx Validation Metrics]]
- [[_COMMUNITY_Margin Verification Scripts|Margin Verification Scripts]]
- [[_COMMUNITY_Backend Core Library Dependencies|Backend Core Library Dependencies]]
- [[_COMMUNITY_SSE Live Streaming Response|SSE Live Streaming Response]]
- [[_COMMUNITY_Document Text Loss Verification|Document Text Loss Verification]]
- [[_COMMUNITY_Workspace Pyright Settings|Workspace Pyright Settings]]
- [[_COMMUNITY_Backend Pyright Config|Backend Pyright Config]]
- [[_COMMUNITY_Backend VS Code Config|Backend VS Code Config]]
- [[_COMMUNITY_Workspace VS Code Config|Workspace VS Code Config]]
- [[_COMMUNITY_Graphify Rule Config|Graphify Rule Config]]
- [[_COMMUNITY_Frontend HTML Entrypoint|Frontend HTML Entrypoint]]
- [[_COMMUNITY_Uploaded Image Assets|Uploaded Image Assets]]
- [[_COMMUNITY_Community 29|Community 29]]

## God Nodes (most connected - your core abstractions)
1. `BaseModel` - 32 edges
2. `DocumentLayout` - 29 edges
3. `TextSpan` - 26 edges
4. `Any` - 23 edges
5. `str` - 23 edges
6. `classify_pdf_layout()` - 23 edges
7. `Template` - 22 edges
8. `ImageAsset` - 22 edges
9. `StyleTokens` - 21 edges
10. `DocumentBlock` - 21 edges

## Surprising Connections (you probably didn't know these)
- `generateFromPrompt()` --references--> `int`  [EXTRACTED]
  vitegrid-frontend/src/api.ts → vitegrid-backend/agent.py
- `DocumentBlock` --uses--> `StyleTokens`  [INFERRED]
  vitegrid-backend/tests/test_audit_programmatic.py → vitegrid-backend/agent.py
- `DocumentLayout` --uses--> `StyleTokens`  [INFERRED]
  vitegrid-backend/tests/test_audit_programmatic.py → vitegrid-backend/agent.py
- `bool` --uses--> `StyleTokens`  [INFERRED]
  vitegrid-backend/tests/test_audit_programmatic.py → vitegrid-backend/agent.py
- `int` --uses--> `StyleTokens`  [INFERRED]
  vitegrid-backend/tests/test_audit_programmatic.py → vitegrid-backend/agent.py

## Communities (29 total, 7 thin omitted)

### Community 0 - "Agent Layout & Style Pipeline"
Cohesion: 0.06
Nodes (104): Client, Enum, GenerateContentConfig, generateFromPrompt(), str, agent1_structural_parse(), agent2_style_evaluate(), agent3_generate_from_prompt() (+96 more)

### Community 1 - "Document PDF/Docx Parser Engine"
Cohesion: 0.06
Nodes (108): DocumentConverter, PageLayout, PdfExtraction, RGBColor, case_column_collision_prevention(), case_dbscan_hdbscan_1d(), case_detect_columns_single(), case_detect_columns_two_column() (+100 more)

### Community 2 - "Frontend UI Components & State"
Cohesion: 0.05
Nodes (66): ChatEditor(), ChatTurn, Props, Dashboard(), Props, HeadlessPreview(), HistoryPanel(), Props (+58 more)

### Community 3 - "Backend FastAPI API & DB Storage"
Cohesion: 0.12
Nodes (47): DeclarativeBase, FastAPI, getTemplate(), importLayout(), jsonOrThrow(), listTemplates(), updateTemplate(), uploadImage() (+39 more)

### Community 4 - "Layout Audit Programmatic Tests"
Cohesion: 0.08
Nodes (55): DocumentBlock, DocumentLayout, case_clean_layout(), case_color_standardization(), case_filter_strips_llm_bottom_edge_messages(), case_font_bounds(), case_low_contrast(), case_markdown_text_runs() (+47 more)

### Community 5 - "Frontend Package Configuration"
Cohesion: 0.08
Nodes (24): dependencies, docx, file-saver, react, react-dom, devDependencies, autoprefixer, postcss (+16 more)

### Community 6 - "Frontend TypeScript Config"
Cohesion: 0.10
Nodes (19): compilerOptions, allowSyntheticDefaultImports, esModuleInterop, isolatedModules, jsx, lib, module, moduleResolution (+11 more)

### Community 7 - "Image Pipeline Integration Tests"
Cohesion: 0.26
Nodes (17): _approved_audit_json(), case_mime_detection(), case_missing_file(), case_partial_block_backfilled_by_defaults(), case_per_element_style_preserved(), case_prompt_contains_three_steps(), expect(), _FakeResponse (+9 more)

### Community 8 - "Remediation & Correction Tests"
Cohesion: 0.19
Nodes (17): case_empty_inputs(), case_intra_word_merge_basic(), case_intra_word_merge_with_kerning_overlap(), case_line_breaks_preserved(), case_prompt_topology_constraints(), case_resume_skill_row_no_fragmentation(), case_word_boundary_preserved(), expect() (+9 more)

### Community 11 - "Frontend Node build tsconfig"
Cohesion: 0.15
Nodes (12): compilerOptions, allowSyntheticDefaultImports, composite, emitDeclarationOnly, lib, module, moduleResolution, outDir (+4 more)

### Community 12 - "VLM Coordinate Transformations"
Cohesion: 0.24
Nodes (11): css_to_vlm_coords(), ioa_score(), pdf_to_vlm_coords(), float, int, Coordinate Projection Transforms  Bidirectional mapping between three coordina, Convert PDF page coordinates to VLM normalized space [0-1000]²., Convert VLM normalized coordinates to CSS pixel space. (+3 more)

### Community 13 - "Frontend Docx Validation Metrics"
Cohesion: 0.27
Nodes (9): calculateColorMatch(), calculateFontMatch(), calculateSpacingDeviation(), calculateTextCoverage(), generateValidationReport(), MANUAL_REVIEW_CHECKLIST, PixelComparisonResult, validateTableStructure() (+1 more)

### Community 14 - "Margin Verification Scripts"
Cohesion: 0.57
Nodes (6): assert(), caseFixedCompilerOutput(), caseMarginEmu(), pxToDxa(), pxToEmu(), unzipDocx()

### Community 15 - "Backend Core Library Dependencies"
Cohesion: 0.29
Nodes (7): Docling, FastAPI, Google GenAI, Playwright, Pydantic, Backend Requirements, SQLAlchemy

### Community 16 - "SSE Live Streaming Response"
Cohesion: 0.33
Nodes (6): demo_iteration_generator(), str, Server-Sent Events (SSE) Streaming for Document Reconstruction, Simulated optimization loop for demo purposes., Formats SSE event dicts as text according to SSE specification., sse_formatter()

### Community 17 - "Document Text Loss Verification"
Cohesion: 0.33
Nodes (5): Any, str, Text-Loss Verification Engine - Validates Docling doesn't omit characters., Compare PyMuPDF ground-truth character stream with Docling parsed structure., verify_document_text_loss()

### Community 18 - "Workspace Pyright Settings"
Cohesion: 0.50
Nodes (3): extraPaths, venv, venvPath

### Community 19 - "Backend Pyright Config"
Cohesion: 0.50
Nodes (3): extraPaths, venv, venvPath

## Knowledge Gaps
- **98 isolated node(s):** `venvPath`, `venv`, `extraPaths`, `python.defaultInterpreterPath`, `python.analysis.extraPaths` (+93 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `generateFromPrompt()` connect `Agent Layout & Style Pipeline` to `Frontend UI Components & State`, `Backend FastAPI API & DB Storage`?**
  _High betweenness centrality (0.170) - this node is a cross-community bridge._
- **Why does `optimize_template_closed_loop()` connect `Agent Layout & Style Pipeline` to `Document PDF/Docx Parser Engine`?**
  _High betweenness centrality (0.139) - this node is a cross-community bridge._
- **Why does `classify_pdf_layout()` connect `Document PDF/Docx Parser Engine` to `Agent Layout & Style Pipeline`?**
  _High betweenness centrality (0.092) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `DocumentLayout` (e.g. with `DocumentBlock` and `DocumentLayout`) actually correct?**
  _`DocumentLayout` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `TextSpan` (e.g. with `PageLayout` and `PdfExtraction`) actually correct?**
  _`TextSpan` has 6 INFERRED edges - model-reasoned connections that need verification._
- **What connects `venvPath`, `venv`, `extraPaths` to the rest of the system?**
  _178 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Agent Layout & Style Pipeline` be split into smaller, more focused modules?**
  _Cohesion score 0.059382819015846536 - nodes in this community are weakly interconnected._