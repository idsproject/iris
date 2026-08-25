package aws

import (
	"context"
	"os"
)

func GetBuckets() ([]string, error) {
	awsConn, err := NewAwsS3Connection(context.Background())
	if err != nil {
		return nil, err
	}

	return awsConn.getBuckets()
}

func ListObjects() ([]BucketObject, error) {
	awsConn, err := NewAwsS3Connection(context.Background())
	if err != nil {
		return nil, err
	}

	bucketName := os.Getenv("PDF_BUCKET_NAME")

	return awsConn.listObjects(bucketName)
}
