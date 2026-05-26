import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { mkdtemp, rm, writeFile, readFile, readdir, stat } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { tmpdir } from "node:os";

const RootList = Type.Optional(
	Type.Array(Type.String({ description: "Path to a root directory" })),
);

const Target = Type.Optional(
	Type.String({ description: "Target function to prove or run; defaults to main" }),
);

const ProveParams = Type.Object({
	source: Type.String({ description: "Complete agent-authored Python source to prove" }),
	trustedRoots: RootList,
	importRoots: RootList,
	target: Target,
});

const RunParams = Type.Object({
	source: Type.String({ description: "Complete agent-authored Python source to prove, approve, and run" }),
	trustedRoots: RootList,
	importRoots: RootList,
	target: Target,
});

const ToolsParams = Type.Object({
	trustedRoots: RootList,
	importRoots: RootList,
});

type RootParams = {
	trustedRoots?: string[];
	importRoots?: string[];
	target?: string;
};

function shellQuote(value: string): string {
	return `'${value.replaceAll("'", "'\\''")}'`;
}

async function fileContains(path: string, text: string): Promise<boolean> {
	try {
		return (await readFile(path, "utf8")).includes(text);
	} catch {
		return false;
	}
}

async function findClauz3ProjectRoot(cwd: string): Promise<string | undefined> {
	let current = resolve(cwd);
	while (true) {
		if (await fileContains(join(current, "pyproject.toml"), 'name = "clauz3"')) {
			return current;
		}
		const parent = dirname(current);
		if (parent === current) return undefined;
		current = parent;
	}
}

async function defaultClauz3Command(ctx: ExtensionContext): Promise<string> {
	const explicit = process.env.CLAUZ3_PI_COMMAND ?? process.env.CLAUZ3_COMMAND;
	if (explicit && explicit.trim()) return explicit.trim();

	const project = process.env.CLAUZ3_PROJECT;
	if (project && project.trim()) {
		return `uv run --project ${shellQuote(resolve(ctx.cwd, project.trim()))} clauz3`;
	}

	const projectRoot = await findClauz3ProjectRoot(ctx.cwd);
	if (projectRoot) {
		return `uv run --project ${shellQuote(projectRoot)} clauz3`;
	}

	return "clauz3";
}

async function discoverTrustedRoots(cwd: string): Promise<string[]> {
	const tools = join(cwd, "tools");
	if (!existsSync(tools)) return [];

	const roots: string[] = [];
	for (const entry of await readdir(tools, { withFileTypes: true })) {
		if (!entry.isDirectory()) continue;
		const candidate = join(tools, entry.name, "trusted");
		try {
			if ((await stat(candidate)).isDirectory()) roots.push(candidate);
		} catch {
			// Ignore non-matching entries.
		}
	}
	return roots;
}

async function walkPythonFiles(root: string): Promise<string[]> {
	const files: string[] = [];
	async function walk(dir: string): Promise<void> {
		for (const entry of await readdir(dir, { withFileTypes: true })) {
			const path = join(dir, entry.name);
			if (entry.isDirectory()) {
				if (entry.name !== "__pycache__") await walk(path);
				continue;
			}
			if (entry.isFile() && entry.name.endsWith(".py") && entry.name !== "__init__.py") {
				files.push(path);
			}
		}
	}
	await walk(root);
	return files.sort();
}

function trustedRelativePath(root: string, path: string): string {
	const parent = resolve(root, "../..");
	return relative(parent, path);
}

function signatureFromArgs(rawArgs: string): string {
	const args = rawArgs
		.split(",")
		.map((arg) => arg.trim())
		.filter((arg) => arg && !arg.startsWith("*") && !arg.includes("="))
		.map((arg) => arg.split(":")[0]?.trim() ?? "")
		.filter(Boolean);
	return `(${args.join(", ")})`;
}

async function listTrustedItems(roots: string[]): Promise<string[]> {
	const items: string[] = [];
	for (const root of roots) {
		if (!existsSync(root)) continue;
		for (const path of await walkPythonFiles(root)) {
			const source = await readFile(path, "utf8");
			const lines = source.split(/\r?\n/);
			for (let index = 0; index < lines.length; index++) {
				const match = /^def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)/.exec(lines[index] ?? "");
				if (!match) continue;
				const decorators = lines.slice(Math.max(0, index - 8), index).map((line) => line.trim());
				const signature = signatureFromArgs(match[2] ?? "");
				const rel = trustedRelativePath(root, path);
				if (decorators.some((line) => line.startsWith("@deal.has") || line.startsWith("@has"))) {
					items.push(`effect ${rel}:${match[1]}${signature}`);
				}
				if (decorators.some((line) => line.startsWith("@contract") || line.startsWith("@clauz3.spec.contract"))) {
					items.push(`contract ${rel}:${match[1]}${signature}`);
				}
			}
		}
	}
	return items;
}

async function rootsFor(ctx: ExtensionContext, params: RootParams): Promise<{ trustedRoots: string[]; importRoots: string[] }> {
	return {
		trustedRoots: params.trustedRoots && params.trustedRoots.length > 0
			? params.trustedRoots.map((p) => resolve(ctx.cwd, p))
			: await discoverTrustedRoots(ctx.cwd),
		importRoots: params.importRoots && params.importRoots.length > 0
			? params.importRoots.map((p) => resolve(ctx.cwd, p))
			: [ctx.cwd],
	};
}

function rootFlags(params: { trustedRoots: string[]; importRoots: string[] }): string[] {
	const flags: string[] = [];
	for (const root of params.trustedRoots) flags.push("--trusted-root", root);
	for (const root of params.importRoots) flags.push("--import-root", root);
	return flags;
}

async function runClauz3(
	pi: ExtensionAPI,
	ctx: ExtensionContext,
	subcommand: string,
	args: string[],
	signal?: AbortSignal,
): Promise<{ command: string; code: number | null; stdout: string; stderr: string }> {
	const base = await defaultClauz3Command(ctx);
	const command = [base, shellQuote(subcommand), ...args.map(shellQuote)].join(" ");
	const result = await pi.exec("bash", ["-lc", command], { signal, timeout: 30_000 });
	return {
		command,
		code: result.code,
		stdout: result.stdout,
		stderr: result.stderr,
	};
}

function textResult(title: string, result: { command: string; code: number | null; stdout: string; stderr: string }): string {
	const parts = [title, `exit: ${result.code ?? "unknown"}`, `command: ${result.command}`];
	if (result.stdout.trim()) parts.push(`stdout:\n${result.stdout.trimEnd()}`);
	if (result.stderr.trim()) parts.push(`stderr:\n${result.stderr.trimEnd()}`);
	return parts.join("\n\n");
}

async function withSourceFile<T>(source: string, fn: (path: string) => Promise<T>): Promise<T> {
	const dir = await mkdtemp(join(tmpdir(), "pi-clauz3-"));
	try {
		const path = join(dir, "program.py");
		await writeFile(path, source, "utf8");
		return await fn(path);
	} finally {
		await rm(dir, { recursive: true, force: true });
	}
}

async function hasClauz3ProjectShape(ctx: ExtensionContext): Promise<boolean> {
	if (await findClauz3ProjectRoot(ctx.cwd)) return true;
	return (await discoverTrustedRoots(ctx.cwd)).length > 0;
}

export default function clauz3Extension(pi: ExtensionAPI) {
	pi.registerTool({
		name: "clauz3_tools",
		label: "ClauZ3 Tools",
		description: "List ClauZ3 trusted tools and contract helpers visible from this project.",
		promptSnippet: "List ClauZ3 trusted tools and contract helpers before writing side-effecting programs.",
		promptGuidelines: [
			"Use clauz3_tools before writing a ClauZ3 program if the available trusted effects or contracts are unclear.",
		],
		parameters: ToolsParams,
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			const roots = await rootsFor(ctx, params);
			const items = await listTrustedItems(roots.trustedRoots);
			const text = items.length > 0 ? items.join("\n") : "No ClauZ3 trusted tools found.";
			return {
				content: [{ type: "text", text }],
				details: { items, ...roots },
			};
		},
	});

	pi.registerTool({
		name: "clauz3_prove",
		label: "ClauZ3 Prove",
		description: "Prove a complete ClauZ3 Python program without executing it.",
		promptSnippet: "Prove ClauZ3 guarantees for an inline Python program without executing trusted side effects.",
		promptGuidelines: [
			"Use clauz3_prove to check a complete ClauZ3 program before clauz3_run.",
			"Programs passed to clauz3_prove should include @clauz3.guarantee(...) decorators on the target function.",
		],
		parameters: ProveParams,
		async execute(_toolCallId, params, signal, _onUpdate, ctx) {
			return await withSourceFile(params.source, async (path) => {
				const roots = await rootsFor(ctx, params);
				const args = [path, ...rootFlags(roots)];
				if (params.target) args.push("--target", params.target);
				const result = await runClauz3(pi, ctx, "prove", args, signal);
				return {
					content: [{ type: "text", text: textResult("clauz3 prove", result) }],
					details: { ...result, ...roots },
				};
			});
		},
	});

	pi.registerTool({
		name: "clauz3_run",
		label: "ClauZ3 Run",
		description: "Prove, request approval for, and run a complete ClauZ3 Python program.",
		promptSnippet: "Run a ClauZ3 program only after proof and user-controlled approval.",
		promptGuidelines: [
			"Use clauz3_run, not direct python execution, when a task requires trusted ClauZ3 side effects.",
			"Do not start, replace, or reconfigure the ClauZ3 approval service unless the user explicitly asks.",
		],
		parameters: RunParams,
		async execute(_toolCallId, params, signal, _onUpdate, ctx) {
			return await withSourceFile(params.source, async (path) => {
				const roots = await rootsFor(ctx, params);
				const args = [path, ...rootFlags(roots)];
				if (params.target) args.push("--target", params.target);
				const result = await runClauz3(pi, ctx, "run", args, signal);
				return {
					content: [{ type: "text", text: textResult("clauz3 run", result) }],
					details: { ...result, ...roots },
				};
			});
		},
	});

	pi.registerCommand("clauz3-tools", {
		description: "List ClauZ3 trusted effects and contract helpers",
		handler: async (_args, ctx) => {
			const roots = await rootsFor(ctx, {});
			const items = await listTrustedItems(roots.trustedRoots);
			ctx.ui.notify(items.length > 0 ? items.join("\n") : "No ClauZ3 trusted tools found.", items.length > 0 ? "info" : "warning");
		},
	});

	pi.registerCommand("clauz3-status", {
		description: "Show ClauZ3 extension status",
		handler: async (_args, ctx) => {
			const command = await defaultClauz3Command(ctx);
			const roots = await rootsFor(ctx, {});
			const service = process.env.CLAUZ3_APPROVAL_SERVICE ?? process.env.CLAUZ3_APPROVAL_URL ?? "not configured";
			ctx.ui.notify(
				[
					`command: ${command}`,
					`cwd: ${ctx.cwd}`,
					`trusted roots: ${roots.trustedRoots.length ? roots.trustedRoots.join(", ") : "none discovered"}`,
					`import roots: ${roots.importRoots.join(", ")}`,
					`approval service: ${service}`,
				].join("\n"),
				"info",
			);
		},
	});

	pi.on("before_agent_start", async (event, ctx) => {
		if (!(await hasClauz3ProjectShape(ctx))) return undefined;
		return {
			systemPrompt: `${event.systemPrompt}\n\n## ClauZ3 side-effect policy\n\nThis project has ClauZ3 trusted roots or is the ClauZ3 repo. When a task requires trusted side effects such as email, banking, database writes, or other audited tools:\n\n- Use clauz3_tools to inspect available trusted effects and contract helpers when needed.\n- Write a complete inline Python program using trusted tools and @clauz3.guarantee(...) decorators.\n- Prefer the strongest true guarantees that match the user's request.\n- Use clauz3_prove before clauz3_run when iterating on a program.\n- Use clauz3_run for execution; do not execute trusted-effect programs directly with python.\n- Do not start, replace, or reconfigure the approval service unless the user explicitly asks.\n`,
		};
	});
}
