package main

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// DiscoverFiles walks baseDir recursively and returns all .jsonl file paths,
// sorted alphabetically (which yields chronological order because the
// directory tree is organised as YYYY-MM/DD/<files>).
func DiscoverFiles(baseDir string) ([]string, error) {
	var files []string

	err := filepath.WalkDir(baseDir, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			return nil
		}
			if strings.HasSuffix(path, ".jsonl") {
			files = append(files, path)
		}
		return nil
	})
	if err != nil {
		return nil, err
	}

	sort.Strings(files)
	return files, nil
}
