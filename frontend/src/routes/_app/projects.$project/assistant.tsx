// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { createFileRoute, Navigate } from "@tanstack/react-router";

function ProjectAssistantPage() {
  const { project } = Route.useParams();
  return <Navigate to="/projects/$project/production" params={{ project }} replace />;
}

export const Route = createFileRoute("/_app/projects/$project/assistant")({
  component: ProjectAssistantPage,
});
