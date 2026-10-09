// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export interface GenerationDebugContext {
  sourceType: 'imageEdit' | 'storyboardGen' | 'imageGen' | 'unknown';
  providerId?: string;
  requestModel?: string;
  requestSize?: string;
  requestAspectRatio?: string;
  prompt?: string;
  extraParams?: Record<string, unknown>;
  referenceImageCount?: number;
  referenceImagePlaceholders?: string[];
  appVersion?: string;
  osName?: string;
  osVersion?: string;
  osBuild?: string;
  userAgent?: string;
}

export const CURRENT_RUNTIME_SESSION_ID = `runtime-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
let runtimeDiagnosticsPromise: Promise<Pick<
  GenerationDebugContext,
  'appVersion' | 'osName' | 'osVersion' | 'osBuild' | 'userAgent'
>> | null = null;

interface BuildGenerationErrorReportInput {
  errorMessage: string;
  errorDetails?: string;
  context?: unknown;
}

function toStringSafe(value: unknown): string {
  if (typeof value === 'string') {
    return value;
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

// Backend bakes the upstream request id into error messages as
// `request_id=<id>; ...`. Pull it out so it can be surfaced prominently in the
// failure UI — it's the single most useful handle for diagnosing a bad run.
export function extractRequestId(message: string | null | undefined): string | null {
  if (!message) return null;
  const match = message.match(/request_id=([^;,\s]+)/i);
  return match ? match[1] : null;
}

export interface GenerationErrorDiagnostics {
  details: string | null;
  requestId: string | null;
  stage: string | null;
  suggestedAction: string | null;
  errorCode: string | null;
  retryable: boolean | null;
}

export function resolveGenerationErrorDiagnostics(
  error: unknown,
  resolvedDetails?: string,
): GenerationErrorDiagnostics {
  const rawErrorMessage =
    error instanceof Error && error.message
      ? error.message.trim()
      : typeof error === 'string'
        ? error.trim()
        : null;
  const details = resolvedDetails?.trim() || rawErrorMessage || null;
  const diagnostic = error instanceof Error
    ? (error as Error & { diagnostic?: Record<string, unknown> }).diagnostic
    : undefined;
  const diagnosticString = (key: string) => {
    const value = diagnostic?.[key];
    return typeof value === 'string' && value.trim() ? value.trim() : null;
  };
  const diagnosticErrorCode = diagnosticString('error_code');
  const diagnosticRetryable = diagnostic?.retryable;

  return {
    details,
    requestId:
      diagnosticString('request_id')
      ?? extractRequestId(rawErrorMessage)
      ?? extractRequestId(resolvedDetails),
    stage: diagnosticString('stage'),
    suggestedAction: diagnosticString('suggested_action'),
    errorCode: diagnosticErrorCode,
    retryable: typeof diagnosticRetryable === 'boolean' ? diagnosticRetryable : null,
  };
}

/** Recover an upstream task handle from diagnostics persisted by older nodes. */
export function extractProviderTaskId(value?: string | null): string | null {
  const text = typeof value === 'string' ? value : '';
  if (!text.trim()) return null;
  const explicit = text.match(
    /(?:provider[_ -]?task[_ -]?id|newapi[_ -]?task[_ -]?id|任务\s*ID|task[_ -]?id)\s*[:=：]\s*([A-Za-z0-9][A-Za-z0-9_.:-]{2,})/i,
  )?.[1];
  if (explicit) return explicit.trim();
  return text.match(/\btask_[A-Za-z0-9_-]{6,}\b/)?.[0] ?? null;
}

/** Read a structured recovery code embedded in a legacy human-readable error. */
export function extractGenerationErrorCode(value?: string | null): string | null {
  const text = typeof value === 'string' ? value : '';
  return text.match(/(?:诊断码|error[_ -]?code)\s*[:=：]\s*([A-Z0-9_:-]+)/i)?.[1] ?? null;
}

export function createReferenceImagePlaceholders(count: number): string[] {
  const safeCount = Math.max(0, Math.min(64, Math.floor(count)));
  return Array.from({ length: safeCount }, (_, index) => `[IMAGE_${index + 1}]`);
}

function parseOsInfo(userAgent: string): { osName: string; osVersion: string } {
  const ua = userAgent || '';

  const windowsMatch = ua.match(/Windows NT ([0-9.]+)/i);
  if (windowsMatch) {
    const ntVersion = windowsMatch[1];
    if (ntVersion.startsWith('10.0')) {
      return { osName: 'Windows', osVersion: '10/11 (NT 10.0)' };
    }
    return { osName: 'Windows', osVersion: `NT ${ntVersion}` };
  }

  const macMatch = ua.match(/Mac OS X ([0-9_]+)/i);
  if (macMatch) {
    return { osName: 'macOS', osVersion: macMatch[1].replace(/_/g, '.') };
  }

  const linuxLike = /Linux|X11/i.test(ua);
  if (linuxLike) {
    return { osName: 'Linux', osVersion: 'unknown' };
  }

  return { osName: 'Unknown', osVersion: 'unknown' };
}

export async function getRuntimeDiagnostics(): Promise<
  Pick<GenerationDebugContext, 'appVersion' | 'osName' | 'osVersion' | 'osBuild' | 'userAgent'>
> {
  if (!runtimeDiagnosticsPromise) {
    runtimeDiagnosticsPromise = (async () => {
      const userAgent = typeof navigator !== 'undefined' ? navigator.userAgent || '' : '';
      const osInfo = parseOsInfo(userAgent);

      let appVersion = typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : 'unknown';
      let resolvedOsName = osInfo.osName;
      let resolvedOsVersion = osInfo.osVersion;
      let resolvedOsBuild = 'unknown';

      return {
        appVersion,
        osName: resolvedOsName,
        osVersion: resolvedOsVersion,
        osBuild: resolvedOsBuild,
        userAgent,
      };
    })();
  }

  return runtimeDiagnosticsPromise;
}

export function buildGenerationErrorReport(
  input: BuildGenerationErrorReportInput
): string {
  const context = (input.context ?? {}) as Partial<GenerationDebugContext>;
  const sections: string[] = [];
  sections.push('# Generation Error Report');
  sections.push('');
  sections.push(`- Error: ${input.errorMessage || 'unknown error'}`);
  if (input.errorDetails) {
    sections.push(`- Details: ${input.errorDetails}`);
  }
  sections.push(`- App Version: ${context.appVersion ?? 'unknown'}`);
  sections.push(`- OS: ${context.osName ?? 'Unknown'} ${context.osVersion ?? 'unknown'}`.trim());
  sections.push(`- OS Build: ${context.osBuild ?? 'unknown'}`);
  sections.push('');
  sections.push('## Request Context');
  sections.push(`- Source: ${context.sourceType ?? 'unknown'}`);
  if (context.providerId) {
    sections.push(`- Provider: ${context.providerId}`);
  }
  if (context.requestModel) {
    sections.push(`- Model: ${context.requestModel}`);
  }
  if (context.requestSize) {
    sections.push(`- Size: ${context.requestSize}`);
  }
  if (context.requestAspectRatio) {
    sections.push(`- Aspect Ratio: ${context.requestAspectRatio}`);
  }
  sections.push(`- Reference Images: ${context.referenceImageCount ?? 0}`);
  if (Array.isArray(context.referenceImagePlaceholders) && context.referenceImagePlaceholders.length > 0) {
    sections.push(`- Reference Image Placeholders: ${context.referenceImagePlaceholders.join(', ')}`);
  }
  sections.push('');
  sections.push('## Prompt');
  sections.push(context.prompt && context.prompt.trim() ? context.prompt : '(empty)');
  sections.push('');
  sections.push('## Extra Params');
  sections.push(
    context.extraParams && Object.keys(context.extraParams).length > 0
      ? toStringSafe(context.extraParams)
      : '{}'
  );
  if (context.userAgent) {
    sections.push('');
    sections.push('## User Agent');
    sections.push(context.userAgent);
  }

  return sections.join('\n');
}
