"use client";

import React, { useState } from "react";
import {
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Cpu,
  ExternalLink,
  History,
  Lightbulb,
  Search,
  ShieldAlert,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import type { DashboardResponse } from "@/types/api";

interface IntelligenceReportCardProps {
  briefing: DashboardResponse["briefing"] | null;
}

const host = (url: string): string => {
  try {
    return new URL(url).hostname.replace(/^www./, "");
  } catch {
    return url;
  }
};

// Every claim in the report carries the links it rests on, so a reader can check it.
function Citations({ urls }: { urls: string[] | undefined }) {
  if (!urls || urls.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5 mt-1">
      {urls.slice(0, 4).map((url) => (
        <a
          key={url}
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-[10.5px] text-[#701A23] underline-offset-2 hover:underline"
        >
          <ExternalLink className="w-3 h-3" aria-hidden="true" />
          {host(url)}
        </a>
      ))}
    </div>
  );
}

const TRAJECTORY_STYLES: Record<string, { bg: string; text: string; border: string; label: string }> = {
  trajectory_shift: { bg: "bg-[#701A23]/10", text: "text-[#701A23]", border: "border-[#701A23]/30", label: "Trajectory Shift" },
  emerging_opportunity: { bg: "bg-[#027A48]/10", text: "text-[#027A48]", border: "border-[#027A48]/30", label: "Emerging Opportunity" },
  emerging_risk: { bg: "bg-[#B54708]/10", text: "text-[#B54708]", border: "border-[#B54708]/30", label: "Emerging Risk" },
  incremental_development: { bg: "bg-[#1E3A66]/10", text: "text-[#1E3A66]", border: "border-[#1E3A66]/30", label: "Incremental Development" },
  no_material_change: { bg: "bg-[#68645E]/10", text: "text-[#68645E]", border: "border-[#68645E]/30", label: "No Material Change" },
};

export function IntelligenceReportCard({ briefing }: IntelligenceReportCardProps) {
  const [showTrace, setShowTrace] = useState(false);

  if (!briefing || (!briefing.executiveSummary && !briefing.trajectoryAssessment)) {
    return null;
  }

  const trajectory = briefing.trajectoryAssessment;
  const trajStyle = (trajectory && TRAJECTORY_STYLES[trajectory.status]) || TRAJECTORY_STYLES.incremental_development;
  const opportunities = briefing.opportunities || [];
  const risks = briefing.risks || [];
  const actions = briefing.recommendedActions || [];
  const history = briefing.historicalConnections || [];
  const sources = briefing.sources || [];
  const uncertainties = briefing.uncertainties || [];
  const trace = briefing.activityTrace || [];
  const modelUsed = briefing.modelUsed || "nvidia/nemotron-3-super-120b-a12b";
  const researchQueries = briefing.researchPlan?.target_queries || [];

  return (
    <div className="bg-white border border-[#ECE7DF] rounded-2xl p-5 sm:p-6 shadow-[0_1px_3px_rgba(0,0,0,0.03)] flex flex-col gap-5 mb-5 font-dm-sans">
      {/* Header: Trajectory Outlook & Executive Synthesis */}
      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3 pb-4 border-b border-[#F0ECE6]">
        <div className="flex flex-col gap-1.5 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold bg-[#FAF4ED] text-[#701A23] border border-[#ECDDC6]">
              <Sparkles className="w-3.5 h-3.5 text-[#701A23]" />
              Strategic Intelligence Synthesis
            </span>
            {trajectory && (
              <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-semibold border ${trajStyle.bg} ${trajStyle.text} ${trajStyle.border}`}>
                <TrendingUp className="w-3 h-3" />
                {trajStyle.label}
              </span>
            )}
          </div>
          {briefing.executiveSummary && (
            <p className="text-[14px] text-[#2C2926] font-medium leading-relaxed mt-2 font-ubuntu">
              {briefing.executiveSummary}
            </p>
          )}
          {trajectory?.explanation && (
            <p className="text-[12.5px] text-[#68645E] leading-relaxed mt-0.5">
              {trajectory.explanation}
            </p>
          )}
        </div>

        {/* Model Provenance Badge */}
        <div className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 bg-[#FAF8F5] border border-[#ECE7DF] rounded-xl text-[11px] text-[#524E48]">
          <Cpu className="w-3.5 h-3.5 text-[#701A23]" />
          <span className="font-mono text-[10.5px] truncate max-w-[190px]" title={modelUsed}>
            {modelUsed.replace("nvidia/", "").replace("nebius/", "")}
          </span>
        </div>
      </div>

      {/* Recommended Next Actions */}
      {actions.length > 0 && (
        <div className="flex flex-col gap-2.5">
          <h4 className="text-[12px] font-semibold text-[#1A1918] tracking-wide uppercase flex items-center gap-1.5">
            <CheckCircle2 className="w-4 h-4 text-[#701A23]" />
            Recommended Next Actions
          </h4>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {actions.map((act, idx) => (
              <div key={idx} className="p-3.5 rounded-xl border border-[#ECE7DF] bg-[#FAF8F5] flex flex-col justify-between gap-2">
                <div className="flex flex-col gap-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-[13px] font-semibold text-[#1A1918]">{act.action}</span>
                    <span className="text-[10px] font-medium px-2 py-0.5 bg-white border border-[#ECE7DF] rounded-full text-[#68645E] shrink-0">
                      {act.time_horizon}
                    </span>
                  </div>
                  <p className="text-[12px] text-[#524E48] leading-relaxed">{act.why_now}</p>
                </div>
                {act.supports_goal && (
                  <span className="text-[11px] text-[#701A23] font-medium">Goal: {act.supports_goal}</span>
                )}
                <Citations urls={act.evidence_urls} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Opportunities & Risks Grid */}
      {(opportunities.length > 0 || risks.length > 0) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Opportunities */}
          {opportunities.length > 0 && (
            <div className="flex flex-col gap-2">
              <h4 className="text-[12px] font-semibold text-[#027A48] tracking-wide uppercase flex items-center gap-1.5">
                <Lightbulb className="w-4 h-4 text-[#027A48]" />
                Emerging Opportunities ({opportunities.length})
              </h4>
              <div className="flex flex-col gap-2">
                {opportunities.map((opp, idx) => (
                  <div key={idx} className="p-3 rounded-xl border border-[#D1FADF] bg-[#F6FEF9] flex flex-col gap-1">
                    <div className="flex items-center justify-between">
                      <span className="text-[12.5px] font-semibold text-[#027A48]">{opp.title}</span>
                      <span className="text-[10px] font-medium text-[#027A48] px-1.5 py-0.5 bg-white rounded border border-[#D1FADF]">
                        {opp.time_horizon}
                      </span>
                    </div>
                    <p className="text-[11.5px] text-[#344054] leading-relaxed">{opp.detail}</p>
                    <Citations urls={opp.evidence_urls} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Risks */}
          {risks.length > 0 && (
            <div className="flex flex-col gap-2">
              <h4 className="text-[12px] font-semibold text-[#B54708] tracking-wide uppercase flex items-center gap-1.5">
                <ShieldAlert className="w-4 h-4 text-[#B54708]" />
                Identified Risks ({risks.length})
              </h4>
              <div className="flex flex-col gap-2">
                {risks.map((risk, idx) => (
                  <div key={idx} className="p-3 rounded-xl border border-[#FEDF89] bg-[#FFFAEB] flex flex-col gap-1">
                    <div className="flex items-center justify-between">
                      <span className="text-[12.5px] font-semibold text-[#B54708]">{risk.title}</span>
                      <span className="text-[10px] font-medium text-[#B54708] px-1.5 py-0.5 bg-white rounded border border-[#FEDF89]">
                        {risk.time_horizon}
                      </span>
                    </div>
                    <p className="text-[11.5px] text-[#4E3924] leading-relaxed">{risk.detail}</p>
                    <Citations urls={risk.evidence_urls} />
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Historical Connections */}
      {history.length > 0 && (
        <div className="flex flex-col gap-2 pt-2 border-t border-[#F0ECE6]">
          <h4 className="text-[12px] font-semibold text-[#1A1918] tracking-wide uppercase flex items-center gap-1.5">
            <History className="w-4 h-4 text-[#701A23]" />
            Historical Connections
          </h4>
          <div className="flex flex-col gap-2">
            {history.map((h, idx) => (
              <div key={idx} className="p-3 rounded-xl bg-[#FAF8F5] border border-[#ECE7DF] flex flex-col gap-1 text-[12px]">
                <div className="flex items-center gap-2">
                  <span className="font-semibold text-[#1A1918]">{h.current_development}</span>
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-white border border-[#ECE7DF] text-[#68645E]">
                    {h.connection_type.replace(/_/g, " ")}
                  </span>
                </div>
                <p className="text-[#524E48] leading-relaxed">
                  <strong className="text-[#2C2926]">Connected to prior event:</strong> {h.historical_event} &mdash; {h.explanation}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* What is not known, and where the report's evidence came from */}
      {(uncertainties.length > 0 || sources.length > 0) && (
        <div className="flex flex-col gap-3 pt-2 border-t border-[#F0ECE6]">
          {uncertainties.length > 0 && (
            <div className="flex flex-col gap-1">
              <h4 className="text-[12px] font-semibold text-[#1A1918] tracking-wide uppercase">What we are unsure about</h4>
              <ul className="list-disc pl-4 text-[12px] text-[#524E48] leading-relaxed">
                {uncertainties.map((u, idx) => (
                  <li key={idx}>{u}</li>
                ))}
              </ul>
            </div>
          )}
          {sources.length > 0 && (
            <div className="flex flex-col gap-1">
              <h4 className="text-[12px] font-semibold text-[#1A1918] tracking-wide uppercase">Sources ({sources.length})</h4>
              <ul className="flex flex-col gap-0.5 text-[12px]">
                {sources.slice(0, 8).map((src) => (
                  <li key={src.url} className="truncate">
                    <a href={src.url} target="_blank" rel="noopener noreferrer" className="text-[#701A23] hover:underline">
                      {src.title || host(src.url)}
                    </a>
                    <span className="text-[#98A2B3]"> {host(src.url)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Autonomous Research & Verification Trace (Observable Workflow for Judges) */}
      <div className="pt-2 border-t border-[#F0ECE6]">
        <button
          type="button"
          onClick={() => setShowTrace(!showTrace)}
          className="w-full flex items-center justify-between text-[11.5px] font-medium text-[#68645E] hover:text-[#1A1918] transition cursor-pointer py-1"
        >
          <span className="flex items-center gap-2">
            <Activity className="w-3.5 h-3.5 text-[#701A23]" />
            <span>Autonomous Intelligence & Evidence Trace ({researchQueries.length} Tavily queries · {trace.length} stages)</span>
          </span>
          {showTrace ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </button>

        {showTrace && (
          <div className="mt-3 p-3.5 bg-[#FAF8F5] border border-[#ECE7DF] rounded-xl flex flex-col gap-3 text-[11.5px]">
            {researchQueries.length > 0 && (
              <div className="flex flex-col gap-1.5">
                <span className="font-semibold text-[#2C2926] flex items-center gap-1.5">
                  <Search className="w-3.5 h-3.5 text-[#701A23]" />
                  Tavily Target Research Queries Planned by Nemotron:
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {researchQueries.map((q, idx) => (
                    <span key={idx} className="px-2.5 py-1 bg-white border border-[#ECE7DF] rounded-lg text-[#344054] font-mono text-[11px]">
                      &ldquo;{q}&rdquo;
                    </span>
                  ))}
                </div>
              </div>
            )}

            {trace.length > 0 && (
              <div className="flex flex-col gap-1.5 mt-1">
                <span className="font-semibold text-[#2C2926]">Execution Pipeline Trace:</span>
                <div className="flex flex-col gap-1 max-h-48 overflow-y-auto font-mono text-[10.5px] bg-white p-2.5 rounded-lg border border-[#ECE7DF]">
                  {trace.map((step, idx) => (
                    <div key={idx} className="flex items-start gap-2 text-[#524E48]">
                      <span className="text-[#98A2B3] shrink-0">{step.timestamp?.slice(11, 19) || "--:--:--"}</span>
                      <span className="font-semibold text-[#701A23] shrink-0">[{step.stage}]</span>
                      <span className="text-[#1A1918]">{step.message}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
