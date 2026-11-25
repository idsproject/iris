package app

import (
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

const (
	fileFoundMsg    = "file found"
	fileNotFoundMsg = "file not found"
)

// TestGetArticleStatus checks how the remediated file and the .error and
// .pdf.error markers combine into a status response.
func TestGetArticleStatus(t *testing.T) {
	const name = "a.pdf"

	tests := []struct {
		name                 string
		wantMsg              string
		files                []string
		wantDone             bool
		wantRemediationError bool
		wantPDFError         bool
	}{
		{
			name:    "nothing yet",
			wantMsg: fileNotFoundMsg,
		},
		{
			name:     fileFoundMsg,
			files:    []string{name},
			wantMsg:  fileFoundMsg,
			wantDone: true,
		},
		{
			name:         "file found, PDF failed",
			files:        []string{name, name + ".pdf.error"},
			wantMsg:      fileFoundMsg,
			wantDone:     true,
			wantPDFError: true,
		},
		{
			name:                 "remediation failed",
			files:                []string{name + ".error"},
			wantMsg:              "error during remediation",
			wantRemediationError: true,
		},
		{
			name:         "PDF failed, file not found",
			files:        []string{name + ".pdf.error"},
			wantMsg:      "error creating PDF",
			wantPDFError: true,
		},
		{
			name:                 "both failed",
			files:                []string{name + ".error", name + ".pdf.error"},
			wantMsg:              "error during remediation",
			wantRemediationError: true,
			wantPDFError:         true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			dir := t.TempDir()
			t.Setenv("DOWNLOAD_DIR", dir)
			for _, f := range tt.files {
				err := os.WriteFile(filepath.Join(dir, f), nil, 0o600)
				if err != nil {
					t.Fatal(err)
				}
			}

			app := &Application{Logger: slog.New(slog.NewTextHandler(io.Discard, nil))}
			router, err := app.routes()
			if err != nil {
				t.Fatal(err)
			}

			req := httptest.NewRequest(http.MethodGet, "/v1/status/"+name, nil)
			rec := httptest.NewRecorder()
			router.ServeHTTP(rec, req)

			if rec.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200; body %s", rec.Code, rec.Body)
			}
			var resp struct {
				RemediationError *bool  `json:"remediation_error"`
				PDFError         *bool  `json:"pdf_error"`
				Message          string `json:"message"`
				Done             bool   `json:"done"`
			}
			err = json.Unmarshal(rec.Body.Bytes(), &resp)
			if err != nil {
				t.Fatalf("decoding %s: %v", rec.Body, err)
			}

			if resp.Message != tt.wantMsg {
				t.Errorf("message = %q, want %q", resp.Message, tt.wantMsg)
			}
			if resp.Done != tt.wantDone {
				t.Errorf("done = %v, want %v", resp.Done, tt.wantDone)
			}
			if got := resp.RemediationError != nil && *resp.RemediationError; got != tt.wantRemediationError {
				t.Errorf("remediation_error = %v, want %v", got, tt.wantRemediationError)
			}
			if got := resp.PDFError != nil && *resp.PDFError; got != tt.wantPDFError {
				t.Errorf("pdf_error = %v, want %v", got, tt.wantPDFError)
			}
		})
	}
}
