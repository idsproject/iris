package aws

import (
	"context"
	"os"
)

const byteToMB = 1048576
const thresholdSize = 10 // in MiB

func UploadArticle(localFilePath string) error {
	awsConn, err := NewAwsS3Connection(context.Background())
	if err != nil {
		return err
	}

	file, err := os.Open(localFilePath)
	if err != nil {
		return err
	}

	fileInfo, err := file.Stat()
	if err != nil {
		return err
	}

	fileSize := fileInfo.Size() / byteToMB
	fileName := fileInfo.Name()
	// fileName := "pdf/" + fileInfo.Name()

	bucketName := os.Getenv("PDF_BUCKET_NAME")

	if fileSize <= thresholdSize {
		err = awsConn.uploadFile(file, bucketName, fileName)
		if err != nil {
			return err
		}
	} else {
		err = awsConn.uploadLargeFile(localFilePath, bucketName, fileName)
		if err != nil {
			return err
		}
	}

	return nil
}
