# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

#!/usr/bin/env python
import boto3
import json
import os
import io
from datetime import datetime, timezone
import math
from base64 import b64decode
from os.path import dirname, join

# calculate a virtual InnerSource score from stars, watches, commits, and issues
def calculateScore(repoData):
    # initial score is 50 to give active repos with low KPIs (forks, watchers, stars) a better starting point
    iScore = 50;
    # weighting: forks and watches count most, then stars, add some little score for open issues, too
    iScore += repoData["forks_count"] * 5;
    
    #Caculate days elapsed since the repo was created and last updated
    repoUpdateDate = datetime.strptime(repoData['updated_at'],"%Y-%m-%dT%H:%M:%SZ")
    repoUpdateDate = repoUpdateDate.replace(tzinfo=timezone.utc)
    repoCreateDate = datetime.strptime(repoData['created_at'],"%Y-%m-%dT%H:%M:%SZ")
    repoCreateDate = repoCreateDate.replace(tzinfo=timezone.utc)
    currentDate = datetime.now(timezone.utc)
    iDaysSinceLastUpdate = currentDate - repoUpdateDate;
    iDaysSinceCreation =  currentDate - repoCreateDate;

    # updated in last 3 months: adds a bonus multiplier between 0..1 to overall score (1 = updated today, 0 = updated more than 100 days ago)
    iScore = iScore * (1 + (100 - min(iDaysSinceLastUpdate.days, 100)) / 100);
    
    # evaluate participation stats 
    # average commits: adds a bonus multiplier between 0..1 to overall score (1 = >10 commits per week, 0 = less than 3 commits per week)
    #iAverageCommitsPerWeek = repo._InnerSourceMetadata.participation.slice(repo._InnerSourceMetadata.participation - 13).reduce((a, b) => a + b) / 13;
    #iScore = iScore * (1 + (min(max(repoData['participation'] - 3, 0), 7)) / 7);
    
    # boost calculation:
    # all repositories updated in the previous year will receive a boost of maximum 1000 declining by days since last update
    iBoost = (1000 - min(iDaysSinceLastUpdate.days, 365) * 2.74);
    
    # gradually scale down boost according to repository creation date to mix with "real" engagement stats
    iBoost *= (365 - min(iDaysSinceCreation.days, 365)) / 365;
    # add boost to score
    iScore += iBoost;
    
    # give projects with a meaningful description a static boost of 50
    if('description' in repoData):
        iScore += 50 if len(repoData['description']) > 200 else 0;
    
    # give projects with contribution guidelines (CONTRIBUTING.md) file a static boost of 100
    if("_InnerSourceMetadata" in repoData and "contributions" in repoData["_InnerSourceMetadata"]):
        iScore += 100 if len(repoData["_InnerSourceMetadata"]["contributions"]) > 1 else 0;
    
    # build in a logarithmic scale for very active projects (open ended but stabilizing around 5000)
    if (iScore > 3000):
        iScore = 3000 + math.log(iScore) * 100;
    
    # final score is a rounded value starting from 0 (subtract the initial value)
    iScore = round(iScore - 50);

    return iScore;

#def lambda_handler(event, context):
if __name__ == "__main__":

    finalData = []
    ccClient = boto3.client('codecommit')
    
    #Get alist of existing repositories
    list_repo_response = ccClient.list_repositories(
        sortBy = 'repositoryName',
        order = 'ascending'
    )    
    repos = list_repo_response["repositories"]
    
    #Iterate over the list of repos
    for repo in repos:
        #this dict will hold each repo related data
        repoData = {}
        
        #Get the repo name
        repoName = repo["repositoryName"]
        #Get repo metadata
        repoDetails = ccClient.get_repository(
            repositoryName = repoName
        )
        repoMetadata = repoDetails["repositoryMetadata"]
        
        #If your CodeCommit installation contains respositories other than the InnerSource repos
        #you may want to filter out the InnerSource ones using tags, such as "type = innersource"
        #Fetch additional info from tags
        tagData = ccClient.list_tags_for_resource(
            resourceArn = repoMetadata["Arn"]
        )
        repoTags = tagData["tags"]
        if "type" in repoTags:
            repoType = repoTags["type"]
            if repoType != "innersource":
                continue
        else:
            continue

        #Populate our dict from metadata
        repoData['id'] = repoMetadata["repositoryId"]
        repoData['name'] = repoMetadata["repositoryName"]
        repoData['full_name'] = repoMetadata["repositoryName"]
        repoData['html_url'] = repoMetadata["cloneUrlHttp"]
        if "repositoryDescription" in repoMetadata:
            repoData['description'] = repoMetadata["repositoryDescription"]
        repoData['created_at'] = repoMetadata["creationDate"]
        repoData['updated_at'] = repoMetadata["lastModifiedDate"]
        repoData['pushed_at'] = repoMetadata["lastModifiedDate"]
        if("defaultBranch" in repoMetadata):
            repoData['default_branch'] = repoMetadata["defaultBranch"]    

        #Get number of branches to mimic fork count
        listOfBranches = ccClient.list_branches(
                repositoryName = repoName
            )
        repoData['forks_count'] =  len(listOfBranches["branches"])
        repoData['_InnerSourceMetadata'] = {}
      
        #If the repository contains an innersource.json, read the file and add to our JSON
        try:            
            innerSourceJsonFile = ccClient.get_file(
                repositoryName = repoName,
                filePath = "innersource.json"
            )

            innerSourceJsonContent = innerSourceJsonFile["fileContent"]
            innerSourceDecoded = innerSourceJsonContent.decode('utf-8')
            innerSourceJson = json.loads(innerSourceDecoded)
            repoData['_InnerSourceMetadata'] = innerSourceJson
            repoData['language'] = innerSourceJson['language']
            repoData['license'] = innerSourceJson["license"]    
            repoData['topics'] = innerSourceJson["topics"]  
            repoData['_InnerSourceMetadata']['score'] = calculateScore(repoData)  

        except (ccClient.exceptions.FileDoesNotExistException, ccClient.exceptions.CommitDoesNotExistException) as e:
            print("Looks like there is no innersource.json in the repo ", repoName, ". You may like to add one.")
        finally:  
            #No CodeCommit equivalents, you may chose to add it via innersource.json
            repoData['stargazers_count'] = ''
            repoData['watchers_count'] = ''        
            repoData['open_issues_count'] = ''  
            repoData['score'] = calculateScore(repoData)
            
            #Without owner the portal breaks, so we give some dummy values
            repoData['owner'] = {}
            repoData['owner']["login"] = "Noble"
            repoData['owner']["avatar_url"] = "./images/demo/Sol.png"   
        
        
        finalData.append(repoData)         
    
    
    #write the content in a json file
    with open("repos.json", "w") as f:
        json.dump(finalData, f, indent=4,sort_keys=False,default=str)
        print("repos.json file written")
