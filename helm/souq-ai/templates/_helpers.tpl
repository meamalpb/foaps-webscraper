{{- define "souq-ai.fullname" -}}
{{ .Chart.Name }}-{{ .Release.Namespace }}
{{- end }}


{{- define "souq-ai.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}


{{- define "souq-ai.labels" -}}
helm.sh/chart: {{ include "souq-ai.chart" . }}
{{ include "souq-ai.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}


{{- define "souq-ai.selectorLabels" -}}
app.kubernetes.io/name: {{ include "souq-ai.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
