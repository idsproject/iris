package prince

import (
	"context"

	"github.com/invopop/princepdf"
)

func CreatePDF(client *princepdf.Client, htmlName string, sourceHtml []byte) ([]byte, error) {
	job := princepdf.NewJob()

	job.Files[htmlName] = sourceHtml

	return client.RunContext(context.Background(), job)
}
