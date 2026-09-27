{{- define "ambuild.name" -}}
{{- printf "%s-web" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ambuild.labels" -}}
app.kubernetes.io/name: ambuild-web
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/part-of: ambuild
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "ambuild.selector" -}}
app.kubernetes.io/name: ambuild-web
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
