{{- define "colaig.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "colaig.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "colaig.labels" -}}
helm.sh/chart: {{ include "colaig.name" . }}-{{ .Chart.Version }}
{{ include "colaig.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "colaig.selectorLabels" -}}
app.kubernetes.io/name: {{ include "colaig.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
